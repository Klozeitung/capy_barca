"""
Permission repository.

All database access for the permission layer lives here.  No HTTP concerns;
the router layer translates exceptions into HTTP responses.
"""
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.permissions.model import BlockPermission, BlockPermissionGrant
from app.users.model import User

# Maximum number of parent hops before giving up on inheritance resolution.
# Guards against infinite loops on corrupted / circular data.
_MAX_DEPTH = 50


# ─── Row-level accessors ──────────────────────────────────────────────────────


def get_permission_row(
    db: Session, block_id: uuid.UUID
) -> Optional[BlockPermission]:
    """Return the explicit permission row for *block_id*, or None."""
    return db.get(BlockPermission, block_id)


def get_grants(db: Session, block_id: uuid.UUID) -> list[uuid.UUID]:
    """Return the list of whitelisted user IDs for *block_id*."""
    row = get_permission_row(db, block_id)
    if row is None:
        return []
    return [g.user_id for g in row.grants]


# ─── Effective permission resolution ─────────────────────────────────────────


def resolve_effective_permission(
    db: Session,
    block_id: uuid.UUID,
) -> tuple[str, Optional[uuid.UUID], list[uuid.UUID]]:
    """
    Walk up the parent chain from *block_id* to find the first explicit
    (non-inherit) permission row.

    Returns
    -------
    tuple[str, Optional[uuid.UUID], list[uuid.UUID]]
        ``(mode, owner_id, granted_user_ids)`` where *mode* is the resolved
        effective mode, *owner_id* is the owner of the resolving block (not
        necessarily *block_id* itself), and *granted_user_ids* is the
        whitelist for that block.

    Falls back to ``('everyone', None, [])`` when no explicit row is found
    before reaching a root block (no parent) or exhausting *_MAX_DEPTH*.
    """
    from app.blocks.models import Block  # local import — avoids circular

    current_id: Optional[uuid.UUID] = block_id
    depth = 0

    while current_id is not None and depth < _MAX_DEPTH:
        perm = get_permission_row(db, current_id)
        block = db.get(Block, current_id)

        if perm is not None:
            owner_id = block.owner_id if block else None
            grants = [g.user_id for g in perm.grants]
            return perm.mode, owner_id, grants

        if block is None:
            break
        current_id = block.parent_id
        depth += 1

    return "everyone", None, []


def find_permission_source(
    db: Session, block_id: Optional[uuid.UUID]
) -> Optional[uuid.UUID]:
    """
    Return the first ancestor block_id (including *block_id* itself) that
    has an explicit permission row, or None if none is found.

    Used by the GET endpoint to populate ``inherited_from_id``.
    """
    from app.blocks.models import Block  # local import — avoids circular

    current: Optional[uuid.UUID] = block_id
    depth = 0
    while current is not None and depth < _MAX_DEPTH:
        if get_permission_row(db, current) is not None:
            return current
        blk = db.get(Block, current)
        if blk is None:
            break
        current = blk.parent_id
        depth += 1
    return None


# ─── Access check ─────────────────────────────────────────────────────────────


def can_user_access(
    db: Session, block_id: uuid.UUID, user: User
) -> bool:
    """
    Return True if *user* is allowed to read *block_id*.

    Admin users bypass all permission checks.  For non-admins the effective
    permission is resolved via :func:`resolve_effective_permission`.
    """
    if user.role == "admin":
        return True

    mode, owner_id, grants = resolve_effective_permission(db, block_id)

    if mode == "everyone":
        return True
    if mode == "private":
        return owner_id is not None and user.id == owner_id
    if mode == "whitelist":
        return (owner_id is not None and user.id == owner_id) or (
            user.id in grants
        )
    # Residual fallback (shouldn't be reached in practice — the workspace root
    # row always anchors the walk with mode='everyone').
    return True


# ─── Accessibility warning ────────────────────────────────────────────────────


_PERMISSIVENESS: dict[str, int] = {"everyone": 2, "whitelist": 1, "private": 0}


def is_more_permissive_than_parent_chain(
    db: "Session",
    block_id: "uuid.UUID",
    own_mode: str,
) -> bool:
    """
    Return True when the block's *own* explicit mode is more permissive than
    the nearest ancestor that has an explicit (non-inherit) permission row.

    Used to warn the user that their block can only be reached via direct link
    because a less permissive ancestor prevents navigation through the tree.

    Examples
    --------
    First-level block: private
      └── Sub-block: inherit   (effective: private)
            └── Leaf: everyone  → warn_accessibility = True

    First-level block: everyone
      └── Sub-block: everyone  → warn_accessibility = False  (not more permissive)
    """
    from app.blocks.models import Block  # local import — avoids circular

    own_level = _PERMISSIVENESS.get(own_mode, -1)
    if own_level < 1:
        # 'private' and 'inherit' can never be more permissive than an ancestor.
        return False

    blk = db.get(Block, block_id)
    if blk is None:
        return False

    current_id: Optional[uuid.UUID] = blk.parent_id
    depth = 0
    while current_id is not None and depth < _MAX_DEPTH:
        perm = get_permission_row(db, current_id)
        if perm is not None:
            ancestor_level = _PERMISSIVENESS.get(perm.mode, -1)
            return own_level > ancestor_level
        anc = db.get(Block, current_id)
        if anc is None:
            break
        current_id = anc.parent_id
        depth += 1
    return False


# ─── Mutations ────────────────────────────────────────────────────────────────


def set_permission(
    db: Session,
    block_id: uuid.UUID,
    mode: str,
    grant_user_ids: list[uuid.UUID],
) -> BlockPermission:
    """
    Upsert the permission row for *block_id* and replace the grant list.

    Existing grants not present in *grant_user_ids* are deleted.
    New grants are inserted.  The caller must commit after this call.
    """
    perm = get_permission_row(db, block_id)
    if perm is None:
        perm = BlockPermission(block_id=block_id, mode=mode)
        db.add(perm)
    else:
        perm.mode = mode

    db.flush()

    # Replace grants atomically.
    existing = (
        db.query(BlockPermissionGrant)
        .filter(BlockPermissionGrant.block_id == block_id)
        .all()
    )
    existing_user_ids = {g.user_id for g in existing}
    new_user_ids = set(grant_user_ids)

    for grant in existing:
        if grant.user_id not in new_user_ids:
            db.delete(grant)
    for uid in new_user_ids - existing_user_ids:
        db.add(BlockPermissionGrant(block_id=block_id, user_id=uid))

    db.flush()
    db.refresh(perm)
    return perm


def delete_permission(db: Session, block_id: uuid.UUID) -> None:
    """
    Remove the explicit permission row for *block_id* (reverts to inherit).

    Grants are removed automatically via CASCADE.
    """
    perm = get_permission_row(db, block_id)
    if perm is not None:
        db.delete(perm)
        db.flush()
