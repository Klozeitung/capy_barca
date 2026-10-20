"""
Block router.

HTTP interface for block operations. Business logic is delegated entirely to
the service layer; this module is responsible only for request parsing, auth
enforcement, response shaping, HTTP error translation, and post-commit
WebSocket broadcast.

Authorization
-------------
Every endpoint resolves the caller through ``get_current_user`` from
``app.session.deps``, the same gate every other router uses, and every handler
that addresses a block by id additionally calls ``require_block_access``. A
valid session establishes who the caller is, not what they may touch.

Read handlers answer 404 for a block the caller may not see, so the response
does not confirm that the id exists. Write handlers answer 403, because
reaching them at all requires already knowing the id; a caller with read
access only gets the read-only detail (``READ_ONLY_DETAIL``).

Access levels: reading covers showing a block, its tree, its history and its
preferences, which hold shared view state such as the folding of the
navigation. Everything that changes a block's content, appearance, place or
state needs write access. Responses carry ``can_write`` for the caller, so the
client can tell what it may edit.

Convention
----------
* Every handler is a synchronous ``def``. FastAPI runs those in a worker
  thread, so their blocking database work never stalls the event loop that
  serves all other requests and the open WebSocket connections.
* Mutation handlers broadcast through ``emit_block_event`` after a successful
  ``db.commit()``, passing their session so the recipient check costs no
  extra connection. The event is then queued for each recipient on the event
  loop; the handler never waits for a client to read it.
* ``upsert_preference`` and ``rebalance_children`` are mutations but do not
  broadcast: preferences are per-client UI state; rebalance is an internal
  position normalisation that the client already handles locally.
"""
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.blocks import repository as repo
from app.blocks import service
from app.blocks import tree as page_tree
from app.blocks.computed import compute_cross_db_dependents, compute_same_db_rollup_dependents
from app.blocks.service import BlockConflict, BlockNotFound
from app.blocks.types import validate_block_type
from app.permissions import repository as perm_repo
from app.permissions.model import READ, WRITE
from app.session.deps import get_current_user, get_db, require_block_access
from app.users.model import User
from app.ws.broadcaster import emit_block_event

logger = logging.getLogger(__name__)


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
#
# ``get_db`` is re-exported from ``app.session.deps`` rather than redefined.
# ``app.permissions.router`` and ``app.automations.automations_router`` import
# it from this module, so the name has to stay resolvable here.


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
    # Whether the caller may change this block. Filled by the endpoints that
    # return blocks (see _mark_write_access). Block snapshots in WebSocket
    # events carry it per recipient (see app.ws.broadcaster).
    can_write: Optional[bool] = None

    model_config = {"from_attributes": True}


class BlockTreeResponse(BaseModel):
    """
    Content of a page as ``GET /{block_id}/tree`` returns it.

    ``loaded`` names every parent whose children are all in ``blocks``;
    ``mentioned`` holds the blocks referenced by inline mentions.
    """

    blocks: list[BlockResponse]
    loaded: list[uuid.UUID]
    mentioned: list[BlockResponse]


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


def _cascade_entry_chip_change(db: Session, block, before: dict) -> list[str]:
    """
    Recompute what depends on a database entry's title or icon after either
    changed.

    Relation chips render the linked entry's title and icon, and formulas and
    rollups that pass a relation through store both in their result. A rename
    or a new icon therefore has to recompute every entry whose computed values
    read this entry, in this database and in others, before the transaction
    commits.

    *before* is the block as ``_block_to_dict`` serialised it before the
    change. Returns the IDs of the databases whose entries now differ, the
    entry's own database included, so the caller can broadcast
    ``database_entries_updated`` for each after the commit. Returns an empty
    list when *block* is not an entry of a database or neither its title nor
    its icon changed.
    """
    before_title = (before.get("content") or {}).get("title")
    after_title = (block.content or {}).get("title")
    if before_title == after_title and before.get("icon") == block.icon:
        return []
    if block.parent_id is None or block.type == "entry_template":
        return []
    parent = repo.get_block(db, block.parent_id)
    if parent is None or parent.type != "database":
        return []

    affected: list[str] = [str(parent.id)]
    compute_same_db_rollup_dependents(db, parent.id, block.id)
    for dep_id in compute_cross_db_dependents(db, parent.id, block.id):
        dep_id_str = str(dep_id)
        if dep_id_str not in affected:
            affected.append(dep_id_str)
    return affected


def _broadcast_entries_updated(db: Session, database_ids: list[str]) -> None:
    """Tell open views of each database in *database_ids* to re-query."""
    for db_id_str in database_ids:
        emit_block_event(
            db=db,
            event_type="database_entries_updated",
            block_id=db_id_str,
            before=None,
            after={"database_id": db_id_str},
        )


def _mark_write_access(db: Session, blocks, user: User):
    """
    Set ``can_write`` on each of *blocks* for *user* and return them.

    One batched permission walk for all of them. The attribute is transient:
    it is not a mapped column and only feeds the response model.
    """
    blocks = list(blocks)
    levels = perm_repo.access_levels(db, (b.id for b in blocks), user)
    for block in blocks:
        block.can_write = levels.get(block.id) == WRITE
    return blocks


def _written(block):
    """Mark a block the caller has just changed, which proves write access."""
    if block is not None:
        block.can_write = True
    return block


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
def create_block(
    payload: BlockCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new block under the given parent."""
    # The new block takes its place in the tree underneath the parent, so the
    # parent is the object the caller has to be allowed to change.
    require_block_access(db, payload.parent_id, current_user, level=WRITE)
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
        emit_block_event(
            db=db,
            event_type="created",
            block_id=str(block.id),
            before=None,
            after=_block_to_dict(block),
        )
        return _written(block)
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.get("/trash", response_model=list[BlockResponse])
def list_trash(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return all top-level trashed blocks (state='trash' whose parent is active or null).

    Only the roots of deleted subtrees are returned; trashed children are
    omitted because restoring a root automatically restores all descendants.
    Results are ordered by most recently updated first.
    Non-admin users only see trashed blocks they own.
    """
    blocks = repo.list_trash(db)
    if current_user.role != "admin":
        blocks = [
            b for b in blocks
            if getattr(b, "owner_id", None) == current_user.id
        ]
    return _mark_write_access(db, blocks, current_user)


@block_router.get("/{block_id}", response_model=BlockResponse)
def get_block(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a single block by ID."""
    block = repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    # 404 rather than 403 on purpose: a read must not confirm that an id the
    # caller may not see exists at all.
    level = perm_repo.access_level(db, block_id, current_user)
    if not perm_repo.satisfies(level, READ):
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    block.can_write = level == WRITE
    return block


@block_router.get("/{block_id}/children", response_model=list[BlockResponse])
def list_children(
    block_id: uuid.UUID,
    state: Optional[str] = "active",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return all direct children of a block, ordered by position.

    The ``state`` query parameter filters by block state. Defaults to
    ``active``. Pass ``state=`` (empty) to return children of all states.
    Children that the current user may not access are silently excluded.

    The parent and all children are checked in one batched permission walk.
    Children without a permission row of their own inherit the parent's, so
    the cost depends on the depth of the tree, not on the number of children.
    """
    try:
        repo.get_block_or_raise(db, block_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    children = repo.list_children(db, block_id, state=state or None)
    levels = perm_repo.access_levels(db, [block_id, *(c.id for c in children)], current_user)
    if not perm_repo.satisfies(levels[block_id], READ):
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    visible = [c for c in children if perm_repo.satisfies(levels[c.id], READ)]
    for child in visible:
        child.can_write = levels[child.id] == WRITE
    return visible


@block_router.get("/{block_id}/tree", response_model=BlockTreeResponse)
def get_tree(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return everything a page shows in one response.

    Covers the active children of *block_id* and, recursively, of every
    layout, column, toggle and synced block among them, including the children
    of a synced mirror's origin. Subpages and databases are not entered. The
    blocks that inline mentions in this content refer to come along in
    ``mentioned``.

    Blocks the caller may not read are left out with everything beneath them,
    as the children endpoint does. Answers 404 for an unknown block and for
    one the caller may not read.
    """
    block = repo.get_block(db, block_id)
    if block is None or not perm_repo.can_user_access(db, block_id, current_user, level=READ):
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    result = page_tree.load_tree(db, block_id, current_user)
    _mark_write_access(db, [*result.blocks, *result.mentioned], current_user)
    return BlockTreeResponse(
        blocks=[BlockResponse.model_validate(b) for b in result.blocks],
        loaded=result.loaded,
        mentioned=[BlockResponse.model_validate(b) for b in result.mentioned],
    )


@block_router.patch("/{block_id}", response_model=BlockResponse)
def update_block(
    block_id: uuid.UUID,
    payload: BlockUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update type, content, position, or state of a block.

    Renaming a database entry also recomputes the formulas and rollups that
    show its title elsewhere, and notifies the open views of every database
    whose entries changed as a result.
    """
    before_block = repo.get_block(db, block_id)
    if before_block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    require_block_access(db, block_id, current_user, level=WRITE)
    try:
        before_snapshot = _block_to_dict(before_block)
        update_kwargs = payload.model_dump(exclude_none=True)
        block = service.update_block_fields(db, block_id, **update_kwargs)
        affected_db_ids = _cascade_entry_chip_change(db, block, before_snapshot)
        db.commit()
        db.refresh(block)
        emit_block_event(
            db=db,
            event_type="content_updated",
            block_id=str(block.id),
            before=before_snapshot,
            after=_block_to_dict(block),
        )
        _broadcast_entries_updated(db, affected_db_ids)
        return _written(block)
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.patch("/{block_id}/appearance", response_model=BlockResponse)
def update_appearance(
    block_id: uuid.UUID,
    payload: BlockAppearanceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update the icon and/or cover of a block.

    A new icon on a database entry also recomputes the formulas and rollups
    that show it on relation chips elsewhere, and notifies the open views of
    every database whose entries changed as a result.
    """
    try:
        before_block = repo.get_block(db, block_id)
        if before_block is None:
            raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
        require_block_access(db, block_id, current_user, level=WRITE)
        before_snapshot = _block_to_dict(before_block)
        block = service.update_block_appearance(
            db,
            block_id,
            icon=payload.icon,
            cover=payload.cover,
        )
        affected_db_ids = _cascade_entry_chip_change(db, block, before_snapshot)
        db.commit()
        emit_block_event(
            db=db,
            event_type="appearance_updated",
            block_id=str(block.id),
            before=before_snapshot,
            after=_block_to_dict(block),
        )
        _broadcast_entries_updated(db, affected_db_ids)
        return _written(block)
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.post("/{block_id}/cover", response_model=BlockResponse)
def upload_cover(
    block_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload (or replace) the cover image for a page block.

    The file is stored at:
        <STATIC_ROOT>/covers/<block_id><ext>

    The upload goes through ``app.media.upload``, the same code path the media
    router uses, so a cover is held to the image allowlist and its body is
    streamed against the size ceiling rather than read whole. Anything the list
    does not name is refused with 415, an oversized body with 413.

    Any previously uploaded cover for this page, whatever its extension, is
    removed once the new one is safely in place. The block's ``cover`` field is
    updated to the static URL of the new file and the updated block is returned.
    """
    import app.media.router as media_module
    from app.media import upload as upload_helper

    static_root: Path = media_module.STATIC_ROOT

    block = repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    require_block_access(db, block_id, current_user, level=WRITE)

    # The type is settled before anything on disk is touched. Checking it later
    # would mean a refused upload had already cost the caller the cover they
    # had, which is a failure mode the check itself would have introduced.
    ext = upload_helper.resolve_extension(file)
    upload_helper.assert_type_permitted(ext, upload_helper.COVER_CATEGORY)

    covers_dir = static_root / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)

    # Streamed beside the target and moved over it only once the whole body has
    # been accepted, so a request that trips the ceiling leaves the previous
    # cover where it was.
    dest = covers_dir / f"{block_id}{ext}"
    staging = upload_helper.staging_path(covers_dir)
    try:
        upload_helper.save_upload(file, staging)
        staging.replace(dest)
    finally:
        staging.unlink(missing_ok=True)

    # Remove any earlier cover for this block that used a different extension.
    for existing in covers_dir.glob(f"{block_id}.*"):
        if existing == dest:
            continue
        try:
            existing.unlink()
        except OSError as exc:
            logger.warning("Could not remove old cover %s: %s", existing, exc)

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
        emit_block_event(
            db=db,
            event_type="appearance_updated",
            block_id=str(updated_block.id),
            before=before_snapshot,
            after=_block_to_dict(updated_block),
        )
        return _written(updated_block)
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.delete("/{block_id}/cover", response_model=BlockResponse)
def remove_cover(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
    require_block_access(db, block_id, current_user, level=WRITE)

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
        return _written(block)

    try:
        before_snapshot = _block_to_dict(block)
        # Set cover to None directly; the service update helpers treat None as
        # "skip this field", so we assign it on the ORM object directly.
        block.cover = None
        db.flush()
        db.commit()
        db.refresh(block)
        emit_block_event(
            db=db,
            event_type="appearance_updated",
            block_id=str(block.id),
            before=before_snapshot,
            after=_block_to_dict(block),
        )
        return _written(block)
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.post("/{block_id}/move", response_model=BlockResponse)
def move_block(
    block_id: uuid.UUID,
    payload: BlockMove,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Move a block to a new parent and position."""
    try:
        before_block = repo.get_block(db, block_id)
        if before_block is None:
            raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
        # Both ends of the move are checked. Permitting only the source would
        # let a caller file a block they own underneath a parent they cannot
        # change; permitting only the target would let them take one away.
        require_block_access(db, block_id, current_user, level=WRITE)
        require_block_access(db, payload.new_parent_id, current_user, level=WRITE)
        before_snapshot = _block_to_dict(before_block)
        block = service.move(
            db,
            block_id,
            new_parent_id=payload.new_parent_id,
            new_position=payload.new_position,
        )
        db.commit()
        emit_block_event(
            db=db,
            event_type="moved",
            block_id=str(block.id),
            before=before_snapshot,
            after=_block_to_dict(block),
        )
        return _written(block)
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.post("/{block_id}/duplicate", response_model=BlockResponse, status_code=201)
def duplicate_block(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Deep-duplicate a block and its entire active subtree.

    Creates a copy of *block_id* immediately after the original in its
    parent's child list, recursively copying the active descendants the
    caller may read. The new root block is returned; children are fetched
    lazily by the client.

    Copying needs read access to the block; placing the copy needs write
    access to the parent it lands in (to the block itself for a block
    without a parent). Each copy takes over the explicit permission row of
    its original, so the copy reaches no account the original did not.

    Raises 404 if the block does not exist.
    """
    original = repo.get_block(db, block_id)
    if original is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    require_block_access(db, block_id, current_user, level=READ)
    require_block_access(db, original.parent_id or block_id, current_user, level=WRITE)

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
        new_block = service.deep_duplicate(
            db,
            block_id,
            parent_id=parent_id,
            position=position,
            owner_id=current_user.id,
            viewer=current_user,
        )
        db.commit()
        emit_block_event(
            db=db,
            event_type="created",
            block_id=str(new_block.id),
            before=None,
            after=_block_to_dict(new_block),
        )
        return _written(new_block)
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)


@block_router.delete("/{block_id}", response_model=DeleteResponse)
def soft_delete_block(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soft-delete a block and all its descendants (sets state to trash)."""
    require_block_access(db, block_id, current_user, level=WRITE)
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
            emit_block_event(
                db=db,
                event_type="state_changed",
                block_id=str(bid),
                before={"state": "active"},
                after={"state": "trash"},
            )
        for db_id_str in cascade_db_ids:
            emit_block_event(
                db=db,
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
def restore_block(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Restore a trashed block and all its trashed descendants."""
    require_block_access(db, block_id, current_user, level=WRITE)
    try:
        restored = service.restore(db, block_id)
        db.commit()
        for bid in restored:
            emit_block_event(
                db=db,
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
def purge_block(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Permanently delete a trashed block and its entire subtree.

    The block must be in state ``trash`` before purging. Returns 204 No Content.
    """
    require_block_access(db, block_id, current_user, level=WRITE)
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

        emit_block_event(
            db=db,
            event_type="purged",
            block_id=str(block_id),
            before=None,
            after=None,
        )

        # Notify any open DatabaseBlock views in target databases that their
        # mirror relation values have changed so they re-query immediately.
        for db_id_str in affected_db_ids:
            emit_block_event(
                db=db,
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
    current_user: User = Depends(get_current_user),
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
    require_block_access(db, block_id, current_user, level=WRITE)
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
    current_user: User = Depends(get_current_user),
):
    """Return a single preference value for a block."""
    block = repo.get_block(db, block_id)
    if block is None or not perm_repo.can_user_access(db, block_id, current_user, level=READ):
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
    current_user: User = Depends(get_current_user),
):
    """
    Create or update a preference value for a block.

    Needs read access only: preferences hold view state such as the folding
    of a navigation entry or the full-width toggle of a page, which every
    reader uses while navigating.
    """
    block = repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    require_block_access(db, block_id, current_user, level=READ)
    pref = repo.upsert_preference(db, block_id, key, payload.value)
    db.commit()
    return pref


@block_router.get(
    "/{block_id}/preferences", response_model=list[PreferenceResponse]
)
def list_preferences(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all preference values for a block."""
    block = repo.get_block(db, block_id)
    if block is None or not perm_repo.can_user_access(db, block_id, current_user, level=READ):
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    return repo.list_preferences(db, block_id)


# ─── History ──────────────────────────────────────────────────────────────────


@block_router.get("/{block_id}/history", response_model=list[EventResponse])
def get_history(
    block_id: uuid.UUID,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the mutation history for a block, newest first.

    Each event carries the full ``before`` and ``after`` content snapshots, so
    this endpoint discloses as much as reading the block itself and is gated
    the same way.
    """
    block = repo.get_block(db, block_id)
    if block is None or not perm_repo.can_user_access(db, block_id, current_user, level=READ):
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
def revert_event(
    block_id: uuid.UUID,
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Revert a block to the ``before`` snapshot of the given event.

    Only fields present in the ``before`` snapshot are modified. Structural
    changes (move, delete) are not reverted via this endpoint – use the
    dedicated move/restore endpoints for those. A revert that changes a
    database entry's title or icon cascades like a rename through
    ``update_block``.
    """
    block = repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    require_block_access(db, block_id, current_user, level=WRITE)
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
        affected_db_ids = _cascade_entry_chip_change(db, block, before_snapshot)
        db.commit()
        db.refresh(block)
        emit_block_event(
            db=db,
            event_type="reverted",
            block_id=str(block.id),
            before=before_snapshot,
            after=_block_to_dict(block),
        )
        _broadcast_entries_updated(db, affected_db_ids)
        return _written(block)
    except Exception as exc:
        db.rollback()
        _handle_service_errors(exc)
