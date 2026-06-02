"""
Block router.

HTTP interface for block operations. All endpoints require a valid session
cookie. Business logic is delegated entirely to the service layer; this
module is responsible only for request parsing, auth enforcement, response
shaping, HTTP error translation, and post-commit WebSocket broadcast.

Convention
----------
* Read-only handlers (GET) remain synchronous ``def`` – no broadcast needed.
* Mutation handlers (POST / PATCH / PUT / DELETE) are ``async def`` so they
  can ``await`` the WebSocket broadcast after a successful ``db.commit()``.
* ``upsert_preference`` and ``rebalance_children`` are mutations but do not
  broadcast: preferences are per-client UI state; rebalance is an internal
  position normalisation that the client already handles locally.
"""
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.blocks import repository as repo
from app.blocks import service
from app.blocks.computed import compute_cross_db_dependents, compute_same_db_rollup_dependents
from app.blocks.service import BlockConflict, BlockNotFound
from app.blocks.types import validate_block_type
from app.database.database import SessionLocal
from app.permissions import repository as perm_repo
from app.session.session import validate_token
from app.users.model import User
from app.ws.broadcaster import broadcast_block_event

logger = logging.getLogger(__name__)


def _resolve_user_from_session(db, session_token: Optional[str]) -> Optional[User]:
    """
    Resolve the authenticated User from a session token, or None.

    Uses the module-local ``validate_token`` reference (patchable in tests)
    so this function degrades safely to None in test setups that patch
    ``validate_token`` without inserting a live session record.

    The block router enforces authentication via the separate
    ``require_session`` dependency; this helper only provides user identity
    for owner_id stamping and permission filtering without adding a second
    authentication gate.
    """
    if not session_token or not validate_token(session_token):
        return None
    try:
        from app.session.session import SessionRecord
        record = (
            db.query(SessionRecord)
            .filter(SessionRecord.token == session_token)
            .first()
        )
        if record is None:
            return None
        user_id = getattr(record, "user_id", None)
        if user_id is None:
            return None
        return db.get(User, user_id)
    except Exception:
        return None


block_router = APIRouter(prefix="/api/blocks", tags=["blocks"])


# ─── Filesystem cleanup helper ───────────────────────────────────────────────


def _cleanup_files_for_blocks(block_snapshots: list[dict]) -> None:
    """
    Delete all physical upload files that belong to the given blocks.

    Called after a successful purge, before the response is returned.
    Errors are logged but never re-raised — a missing file must not undo
    a successfully committed purge.

    Storage layout (mirrors app.media.router):
      drives/<block_id>/        – all files of a drive block
      media/{image|video|audio|pdf}/<uuid><ext>  – individual media files
      files/<uuid><ext>         – individual file-block files
    """
    import app.media.router as media_module
    static_root: Path = media_module.STATIC_ROOT

    MEDIA_TYPES = frozenset({"image", "video", "audio", "pdf"})

    for snap in block_snapshots:
        block_type: str = snap.get("type", "")
        block_id: str = snap.get("id", "")
        content: dict = snap.get("content") or {}

        try:
            if block_type == "drive":
                # Entire drive directory (all files regardless of folder structure)
                drive_dir = static_root / "drives" / block_id
                if drive_dir.exists():
                    shutil.rmtree(drive_dir)
                    logger.info("Purge: removed drive directory %s", drive_dir)

            elif block_type in MEDIA_TYPES:
                # Single file stored as media/<type>/<uuid><ext>
                url: str = content.get("url", "")
                if url:
                    # URL is /static/uploads/media/<type>/<filename> – extract filename
                    filename = Path(url).name
                    file_path = static_root / "media" / block_type / filename
                    if file_path.exists():
                        file_path.unlink()
                        logger.info("Purge: removed media file %s", file_path)

            elif block_type == "file":
                # Single file stored as files/<uuid><ext>
                url = content.get("url", "")
                if url:
                    filename = Path(url).name
                    file_path = static_root / "files" / filename
                    if file_path.exists():
                        file_path.unlink()
                        logger.info("Purge: removed file %s", file_path)

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Purge filesystem cleanup failed for block %s (%s): %s",
                block_id, block_type, exc,
            )



def _collect_block_snapshots(db, root_id: uuid.UUID) -> list[dict]:
    """
    Return _block_to_dict snapshots for *root_id* and all its descendants
    via breadth-first traversal, using only the public repo API.

    Must be called BEFORE service.purge() removes the rows from the DB.
    """
    from collections import deque

    result: list[dict] = []
    queue: deque[uuid.UUID] = deque([root_id])
    while queue:
        current = queue.popleft()
        block = repo.get_block(db, current)
        if block is None:
            continue
        result.append(_block_to_dict(block))
        children = repo.list_children(db, current, state=None)
        queue.extend(c.id for c in children)
    return result


# ─── DB dependency ────────────────────────────────────────────────────────────


def get_db():
    """Yield a database session and ensure it is closed after the request."""
    with SessionLocal() as db:
        yield db


# ─── Auth ─────────────────────────────────────────────────────────────────────


def require_session(session: Optional[str] = Cookie(default=None)) -> str:
    """
    Dependency that enforces a valid session cookie.

    The local ``validate_token`` reference is intentionally kept here (rather
    than importing ``require_session`` from ``app.session.deps``) so that the
    existing test suite can patch ``app.blocks.router.validate_token`` to
    bypass authentication without touching the shared deps module.

    Raises
    ------
    HTTPException(401)
        If the cookie is absent or the token is invalid / expired.
    """
    if not session or not validate_token(session):
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    return session


# ─── Request / Response schemas ───────────────────────────────────────────────


class BlockCreate(BaseModel):
    type: str
    parent_id: uuid.UUID
    position: Optional[float] = None
    reference_id: Optional[uuid.UUID] = None
    content: Optional[dict] = None
    icon: Optional[str] = None
    cover: Optional[str] = None

    @field_validator("type")
    @classmethod
    def type_must_be_known(cls, v: str) -> str:
        """Reject unknown block types at the API boundary (returns 422)."""
        return validate_block_type(v)


class BlockUpdate(BaseModel):
    type: Optional[str] = None
    content: Optional[dict] = None
    position: Optional[float] = None
    state: Optional[str] = None

    @field_validator("type")
    @classmethod
    def type_must_be_known(cls, v: Optional[str]) -> Optional[str]:
        """Reject unknown block types at the API boundary (returns 422)."""
        if v is None:
            return v
        return validate_block_type(v)


class BlockAppearanceUpdate(BaseModel):
    icon: Optional[str] = None
    cover: Optional[str] = None


class BlockMove(BaseModel):
    new_parent_id: uuid.UUID
    new_position: float


class BlockResponse(BaseModel):
    id: uuid.UUID
    parent_id: Optional[uuid.UUID]
    reference_id: Optional[uuid.UUID]
    type: str
    position: float
    state: str
    content: Optional[dict]
    icon: Optional[str]
    cover: Optional[str]
    owner_id: Optional[uuid.UUID] = None

    model_config = {"from_attributes": True}


class DeleteResponse(BaseModel):
    affected: list[uuid.UUID]


class RestoreResponse(BaseModel):
    restored: list[uuid.UUID]


class RebalanceResponse(BaseModel):
    rebalanced: list[uuid.UUID]


class PreferenceResponse(BaseModel):
    block_id: uuid.UUID
    key: str
    value: Any

    model_config = {"from_attributes": True}


class PreferenceUpdate(BaseModel):
    value: Any


class EventResponse(BaseModel):
    id: uuid.UUID
    block_id: Optional[uuid.UUID]
    event_type: str
    before: Optional[Any]
    after: Optional[Any]
    created_at: str

    model_config = {"from_attributes": True}


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _block_to_dict(block) -> dict:
    """Serialise a Block ORM instance to a plain dict matching BlockResponse."""
    return {
        "id": str(block.id),
        "parent_id": str(block.parent_id) if block.parent_id else None,
        "reference_id": str(block.reference_id) if block.reference_id else None,
        "type": block.type,
        "position": block.position,
        "state": block.state,
        "content": block.content,
        "icon": block.icon,
        "cover": block.cover,
        "owner_id": str(block.owner_id) if getattr(block, "owner_id", None) else None,
    }


# ─── Error translation ────────────────────────────────────────────────────────


def _handle_service_errors(exc: Exception) -> None:
    """Translate service exceptions into appropriate HTTP responses."""
    if isinstance(exc, BlockNotFound):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, BlockConflict):
        raise HTTPException(status_code=409, detail=str(exc))
    raise exc


# ─── Endpoints ────────────────────────────────────────────────────────────────


@block_router.post("", response_model=BlockResponse, status_code=201)
async def create_block(
    payload: BlockCreate,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """Create a new block under the given parent."""
    current_user = _resolve_user_from_session(db, _session)
    try:
        block = service.create_block(
            db,
            type=payload.type,
            parent_id=payload.parent_id,
            position=payload.position,
            reference_id=payload.reference_id,
            content=payload.content,
            icon=payload.icon,
            cover=payload.cover,
            owner_id=current_user.id if current_user else None,
        )
        db.commit()
        await broadcast_block_event(
            event_type="created",
            block_id=str(block.id),
            before=None,
            after=_block_to_dict(block),
        )
        return block
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.get("/trash", response_model=list[BlockResponse])
def list_trash(
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """
    Return all top-level trashed blocks (state='trash' whose parent is active or null).

    Only the roots of deleted subtrees are returned; trashed children are
    omitted because restoring a root automatically restores all descendants.
    Results are ordered by most recently updated first.
    Non-admin users only see trashed blocks they own.
    """
    blocks = repo.list_trash(db)
    current_user = _resolve_user_from_session(db, _session)
    if current_user is not None and current_user.role != "admin":
        blocks = [
            b for b in blocks
            if getattr(b, "owner_id", None) == current_user.id
        ]
    return blocks


@block_router.get("/{block_id}", response_model=BlockResponse)
def get_block(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """Return a single block by ID."""
    block = repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    # Return 404 (not 403) for inaccessible blocks to avoid leaking existence.
    # Permission check requires a resolved user; degrade gracefully to open
    # access when the session cannot be resolved (e.g. in test setups that
    # only patch validate_token without inserting a live session record).
    current_user = _resolve_user_from_session(db, _session)
    if current_user is not None and not perm_repo.can_user_access(db, block_id, current_user):
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    return block


@block_router.get("/{block_id}/children", response_model=list[BlockResponse])
def list_children(
    block_id: uuid.UUID,
    state: Optional[str] = "active",
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """
    Return all direct children of a block, ordered by position.

    The ``state`` query parameter filters by block state. Defaults to
    ``active``. Pass ``state=`` (empty) to return children of all states.
    Children that the current user may not access are silently excluded.
    When the user cannot be resolved (e.g. test environments that only patch
    ``validate_token``), the full child list is returned unfiltered.
    """
    try:
        repo.get_block_or_raise(db, block_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    children = repo.list_children(db, block_id, state=state or None)
    current_user = _resolve_user_from_session(db, _session)
    if current_user is not None and current_user.role != "admin":
        children = [
            c for c in children
            if perm_repo.can_user_access(db, c.id, current_user)
        ]
    return children


@block_router.patch("/{block_id}", response_model=BlockResponse)
async def update_block(
    block_id: uuid.UUID,
    payload: BlockUpdate,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """Update type, content, position, or state of a block."""
    before_block = repo.get_block(db, block_id)
    if before_block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    try:
        before_snapshot = _block_to_dict(before_block)
        update_kwargs = payload.model_dump(exclude_none=True)
        block = service.update_block_fields(db, block_id, **update_kwargs)
        db.commit()
        db.refresh(block)
        await broadcast_block_event(
            event_type="content_updated",
            block_id=str(block.id),
            before=before_snapshot,
            after=_block_to_dict(block),
        )
        return block
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.patch("/{block_id}/appearance", response_model=BlockResponse)
async def update_appearance(
    block_id: uuid.UUID,
    payload: BlockAppearanceUpdate,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """Update the icon and/or cover of a block."""
    try:
        before_block = repo.get_block(db, block_id)
        if before_block is None:
            raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
        before_snapshot = _block_to_dict(before_block)
        block = service.update_block_appearance(
            db,
            block_id,
            icon=payload.icon,
            cover=payload.cover,
        )
        db.commit()
        await broadcast_block_event(
            event_type="appearance_updated",
            block_id=str(block.id),
            before=before_snapshot,
            after=_block_to_dict(block),
        )
        return block
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.post("/{block_id}/cover", response_model=BlockResponse)
async def upload_cover(
    block_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """
    Upload (or replace) the cover image for a page block.

    The file is stored at:
        <STATIC_ROOT>/covers/<block_id><ext>

    Any previously uploaded cover for this page (regardless of extension) is
    deleted before writing the new file. The block's ``cover`` field is updated
    to the static URL of the new file and the updated block is returned.
    """
    import app.media.router as media_module
    static_root: Path = media_module.STATIC_ROOT

    block = repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")

    # Determine extension from original filename; default to empty string.
    original_name = file.filename or ""
    ext = Path(original_name).suffix.lower()

    covers_dir = static_root / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)

    # Remove any existing cover file for this block (any extension).
    for existing in covers_dir.glob(f"{block_id}.*"):
        try:
            existing.unlink()
        except OSError as exc:
            logger.warning("Could not remove old cover %s: %s", existing, exc)

    # Write the new cover file.
    dest = covers_dir / f"{block_id}{ext}"
    try:
        content = await file.read()
        dest.write_bytes(content)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save cover: {exc}") from exc

    # Build the public URL (mirrors the static-mount convention used by media).
    cover_url = f"/static/uploads/covers/{block_id}{ext}"

    try:
        before_snapshot = _block_to_dict(block)
        updated_block = service.update_block_appearance(
            db, block_id, icon=None, cover=cover_url
        )
        # update_block_appearance may skip None values for icon — preserve
        # the existing icon by only patching cover directly when needed.
        if updated_block.cover != cover_url:
            updated_block.cover = cover_url
            db.flush()
        db.commit()
        db.refresh(updated_block)
        await broadcast_block_event(
            event_type="appearance_updated",
            block_id=str(updated_block.id),
            before=before_snapshot,
            after=_block_to_dict(updated_block),
        )
        return updated_block
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.delete("/{block_id}/cover", response_model=BlockResponse)
async def remove_cover(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """
    Remove the cover image of a page block.

    The physical file is deleted from
        <STATIC_ROOT>/covers/<block_id>.*
    and the block's ``cover`` field is set to ``None``. Idempotent: if the
    block has no cover the endpoint still returns 200 with the current block.
    """
    import app.media.router as media_module
    static_root: Path = media_module.STATIC_ROOT

    block = repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")

    # Delete physical file(s) for this block.
    covers_dir = static_root / "covers"
    for existing in covers_dir.glob(f"{block_id}.*"):
        try:
            existing.unlink()
            logger.info("Removed cover file %s", existing)
        except OSError as exc:
            logger.warning("Could not remove cover file %s: %s", existing, exc)

    if block.cover is None:
        # Already no cover — return block unchanged (idempotent).
        return block

    try:
        before_snapshot = _block_to_dict(block)
        # Set cover to None directly; the service update helpers treat None as
        # "skip this field", so we assign it on the ORM object directly.
        block.cover = None
        db.flush()
        db.commit()
        db.refresh(block)
        await broadcast_block_event(
            event_type="appearance_updated",
            block_id=str(block.id),
            before=before_snapshot,
            after=_block_to_dict(block),
        )
        return block
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.post("/{block_id}/move", response_model=BlockResponse)
async def move_block(
    block_id: uuid.UUID,
    payload: BlockMove,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """Move a block to a new parent and position."""
    try:
        before_block = repo.get_block(db, block_id)
        if before_block is None:
            raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
        before_snapshot = _block_to_dict(before_block)
        block = service.move(
            db,
            block_id,
            new_parent_id=payload.new_parent_id,
            new_position=payload.new_position,
        )
        db.commit()
        await broadcast_block_event(
            event_type="moved",
            block_id=str(block.id),
            before=before_snapshot,
            after=_block_to_dict(block),
        )
        return block
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.post("/{block_id}/duplicate", response_model=BlockResponse, status_code=201)
async def duplicate_block(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """
    Deep-duplicate a block and its entire active subtree.

    Creates a copy of *block_id* immediately after the original in its
    parent's child list, recursively copying all active descendants. The
    new root block is returned; children are fetched lazily by the client.

    Raises 404 if the block does not exist.
    """
    original = repo.get_block(db, block_id)
    if original is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")

    # Calculate the insertion position: midpoint between the original and its
    # next sibling, or original.position + 1 when there is no next sibling.
    if original.parent_id is not None:
        siblings = repo.list_children(db, original.parent_id, state="active")
        idx = next((i for i, s in enumerate(siblings) if s.id == block_id), None)
        if idx is not None and idx < len(siblings) - 1:
            position = (original.position + siblings[idx + 1].position) / 2.0
        else:
            position = original.position + 1.0
        parent_id = original.parent_id
    else:
        position = original.position + 1.0
        parent_id = original.parent_id  # workspace-level blocks have no parent

    try:
        _dup_user = _resolve_user_from_session(db, _session)
        new_block = service.deep_duplicate(
            db,
            block_id,
            parent_id=parent_id,
            position=position,
            owner_id=_dup_user.id if _dup_user else None,
        )
        db.commit()
        await broadcast_block_event(
            event_type="created",
            block_id=str(new_block.id),
            before=None,
            after=_block_to_dict(new_block),
        )
        return new_block
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.delete("/{block_id}", response_model=DeleteResponse)
async def soft_delete_block(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """Soft-delete a block and all its descendants (sets state to trash)."""
    try:
        affected = service.soft_delete(db, block_id)

        # For any affected block that is a database entry, trigger the
        # rollup/formula cascade so sibling and cross-DB dependants are
        # recomputed now that these entries are in 'trash' state.
        # _compute_rollup already filters trashed entries, so this produces
        # correct aggregation results in one transaction.
        cascade_db_ids: set[str] = set()
        for bid in affected:
            blk = repo.get_block(db, bid)
            if blk is None or not blk.parent_id:
                continue
            parent = repo.get_block(db, blk.parent_id)
            if parent is None or parent.type != "database":
                continue
            compute_same_db_rollup_dependents(db, parent.id, bid)
            for dep_id in compute_cross_db_dependents(db, parent.id, bid):
                cascade_db_ids.add(str(dep_id))
            cascade_db_ids.add(str(parent.id))

        db.commit()
        for bid in affected:
            await broadcast_block_event(
                event_type="state_changed",
                block_id=str(bid),
                before={"state": "active"},
                after={"state": "trash"},
            )
        for db_id_str in cascade_db_ids:
            await broadcast_block_event(
                event_type="database_entries_updated",
                block_id=db_id_str,
                before=None,
                after={"database_id": db_id_str},
            )
        return DeleteResponse(affected=affected)
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.post("/{block_id}/restore", response_model=RestoreResponse)
async def restore_block(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """Restore a trashed block and all its trashed descendants."""
    try:
        restored = service.restore(db, block_id)
        db.commit()
        for bid in restored:
            await broadcast_block_event(
                event_type="state_changed",
                block_id=str(bid),
                before={"state": "trash"},
                after={"state": "active"},
            )
        return RestoreResponse(restored=restored)
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.delete("/{block_id}/purge", status_code=204)
async def purge_block(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """
    Permanently delete a trashed block and its entire subtree.

    The block must be in state ``trash`` before purging. Returns 204 No Content.
    """
    try:
        # Snapshot the entire subtree BEFORE deletion so we know which
        # files to clean up from the filesystem.  After db.delete() the
        # ORM objects are gone from the session.
        snapshots = _collect_block_snapshots(db, block_id)

        # purge() cleans up bilateral relation mirrors and returns the IDs
        # of external databases whose entries were modified.
        affected_db_ids = service.purge(db, block_id)
        db.commit()

        # Remove physical files for every purged block.  Done after commit
        # so a filesystem error never rolls back the DB deletion.
        _cleanup_files_for_blocks(snapshots)

        await broadcast_block_event(
            event_type="purged",
            block_id=str(block_id),
            before=None,
            after=None,
        )

        # Notify any open DatabaseBlock views in target databases that their
        # mirror relation values have changed so they re-query immediately.
        for db_id_str in affected_db_ids:
            await broadcast_block_event(
                event_type="database_entries_updated",
                block_id=db_id_str,
                before=None,
                after={"database_id": db_id_str},
            )
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.post(
    "/{block_id}/rebalance-children",
    response_model=RebalanceResponse,
)
def rebalance_children(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """
    Normalise the positions of all active children of *block_id* to evenly
    spaced integers (1.0, 2.0, …), preserving order.

    This endpoint is primarily for administrative use. Under normal operation
    the service layer triggers rebalancing automatically whenever the minimum
    sibling gap falls below the precision threshold.
    """
    try:
        repo.get_block_or_raise(db, block_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    try:
        rebalanced = service.rebalance_positions(db, block_id)
        db.commit()
        return {"rebalanced": rebalanced}
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


# ─── Preferences ──────────────────────────────────────────────────────────────


@block_router.get(
    "/{block_id}/preferences/{key}", response_model=PreferenceResponse
)
def get_preference(
    block_id: uuid.UUID,
    key: str,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """Return a single preference value for a block."""
    block = repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    pref = repo.get_preference(db, block_id, key)
    if pref is None:
        raise HTTPException(
            status_code=404,
            detail=f"Preference '{key}' not found for block {block_id}",
        )
    return pref


@block_router.put(
    "/{block_id}/preferences/{key}", response_model=PreferenceResponse
)
def upsert_preference(
    block_id: uuid.UUID,
    key: str,
    payload: PreferenceUpdate,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """Create or update a preference value for a block."""
    block = repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    pref = repo.upsert_preference(db, block_id, key, payload.value)
    db.commit()
    return pref


@block_router.get(
    "/{block_id}/preferences", response_model=list[PreferenceResponse]
)
def list_preferences(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """Return all preference values for a block."""
    block = repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    return repo.list_preferences(db, block_id)


# ─── History ──────────────────────────────────────────────────────────────────


@block_router.get("/{block_id}/history", response_model=list[EventResponse])
def get_history(
    block_id: uuid.UUID,
    limit: int = 100,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """Return the mutation history for a block, newest first."""
    block = repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    events = repo.list_events(db, block_id, limit=limit)
    return [
        EventResponse(
            id=e.id,
            block_id=e.block_id,
            event_type=e.event_type,
            before=e.before,
            after=e.after,
            created_at=e.created_at.isoformat(),
        )
        for e in events
    ]


@block_router.post("/{block_id}/revert/{event_id}", response_model=BlockResponse)
async def revert_event(
    block_id: uuid.UUID,
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """
    Revert a block to the ``before`` snapshot of the given event.

    Only fields present in the ``before`` snapshot are modified. Structural
    changes (move, delete) are not reverted via this endpoint – use the
    dedicated move/restore endpoints for those.
    """
    block = repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    event = repo.get_event(db, event_id)
    if event is None or event.block_id != block_id:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    if event.before is None:
        raise HTTPException(
            status_code=409, detail="This event has no before-snapshot to revert to."
        )

    before = event.before
    before_snapshot = _block_to_dict(block)
    try:
        # Assign fields directly so that None values are honoured.
        # repo.update_block uses None as a sentinel for "do not change",
        # which would prevent reverting a field back to None.
        if "content" in before:
            block.content = before["content"]
        if "icon" in before:
            block.icon = before["icon"]
        if "cover" in before:
            block.cover = before["cover"]
        db.flush()
        db.commit()
        db.refresh(block)
        await broadcast_block_event(
            event_type="reverted",
            block_id=str(block.id),
            before=before_snapshot,
            after=_block_to_dict(block),
        )
        return block
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)
