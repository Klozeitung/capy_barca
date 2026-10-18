"""
Permission repository.

All database access for the permission layer lives here.  No HTTP concerns;
the router layer translates exceptions into HTTP responses.
"""
import uuid
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

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


@dataclass(frozen=True)
class EffectivePermission:
    """
    The permission that governs a block, after inheritance is resolved.

    ``owner_id`` is the owner of the block that carries the explicit row, which
    is not necessarily the block the question was asked about. ``grants`` keeps
    the order of the stored grant rows.

    The value does not depend on who asks, so one instance answers the access
    question for any number of users (see :meth:`admits`).
    """

    mode: str
    owner_id: Optional[uuid.UUID]
    grants: tuple[uuid.UUID, ...]

    def admits(self, user: User) -> bool:
        """Return True if *user* may read a block governed by this permission."""
        if user.role == "admin":
            return True
        if self.mode == "everyone":
            return True
        if self.mode == "private":
            return self.owner_id is not None and user.id == self.owner_id
        if self.mode == "whitelist":
            return (self.owner_id is not None and user.id == self.owner_id) or (
                user.id in self.grants
            )
        # Residual fallback (shouldn't be reached in practice — the workspace
        # root row always anchors the walk with mode='everyone').
        return True


# Returned when no explicit row is found before the chain ends or the depth
# limit is reached.
_UNRESTRICTED = EffectivePermission(mode="everyone", owner_id=None, grants=())


def resolve_effective_permissions(
    db: Session,
    block_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, EffectivePermission]:
    """
    Resolve the effective permission of many blocks at once.

    The parent chains of all requested blocks are walked together, one level
    at a time. Each level costs two queries (the blocks themselves and their
    permission rows) plus one for the grants of the rows found, however many
    blocks share that level. Siblings converge on their common parent after
    the first level, so listing the children of a page costs the depth of the
    tree in queries, not the number of children times that depth.

    Every requested id is present in the result. A block that does not exist
    and carries no permission row resolves to ``everyone``, as a missing chain
    always has.

    The walk for each block examines at most ``_MAX_DEPTH`` blocks, including
    itself, and stops at a block it has already visited, so corrupted or
    circular parent links cannot loop.
    """
    from app.blocks.models import Block  # local import — avoids circular

    requested = list(dict.fromkeys(block_ids))
    if not requested:
        return {}

    parent_of: dict[uuid.UUID, Optional[uuid.UUID]] = {}
    owner_of: dict[uuid.UUID, Optional[uuid.UUID]] = {}
    explicit: dict[uuid.UUID, BlockPermission] = {}
    examined: set[uuid.UUID] = set()

    frontier = set(requested)
    for _ in range(_MAX_DEPTH):
        frontier -= examined
        if not frontier:
            break
        examined |= frontier

        for block_id, parent_id, owner_id in db.execute(
            select(Block.id, Block.parent_id, Block.owner_id).where(
                Block.id.in_(frontier)
            )
        ):
            parent_of[block_id] = parent_id
            owner_of[block_id] = owner_id

        for perm in db.scalars(
            select(BlockPermission)
            .where(BlockPermission.block_id.in_(frontier))
            .options(selectinload(BlockPermission.grants))
        ):
            explicit[perm.block_id] = perm

        # Only blocks without an explicit row inherit, so only their parents
        # need to be examined on the next level.
        frontier = {
            parent_of[block_id]
            for block_id in frontier
            if block_id not in explicit and parent_of.get(block_id) is not None
        }

    def _resolve(start: uuid.UUID) -> EffectivePermission:
        current: Optional[uuid.UUID] = start
        visited: set[uuid.UUID] = set()
        while current is not None and len(visited) < _MAX_DEPTH and current not in visited:
            visited.add(current)
            perm = explicit.get(current)
            if perm is not None:
                return EffectivePermission(
                    mode=perm.mode,
                    owner_id=owner_of.get(current),
                    grants=tuple(g.user_id for g in perm.grants),
                )
            if current not in parent_of:
                # The block does not exist; its chain ends here.
                break
            current = parent_of[current]
        return _UNRESTRICTED

    return {block_id: _resolve(block_id) for block_id in requested}


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
    Single-block form of :func:`resolve_effective_permissions`.
    """
    effective = resolve_effective_permissions(db, [block_id])[block_id]
    return effective.mode, effective.owner_id, list(effective.grants)


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

    Admin users bypass all permission checks without touching the database.
    For non-admins the effective permission is resolved via
    :func:`resolve_effective_permissions`.
    """
    if user.role == "admin":
        return True
    return resolve_effective_permissions(db, [block_id])[block_id].admits(user)


def filter_accessible(
    db: Session, block_ids: Iterable[uuid.UUID], user: User
) -> set[uuid.UUID]:
    """
    Return the subset of *block_ids* that *user* may read.

    Resolves all of them in one batched walk instead of one walk per block.
    Admins receive the full set without a query.
    """
    ids = set(block_ids)
    if user.role == "admin" or not ids:
        return ids
    resolved = resolve_effective_permissions(db, ids)
    return {block_id for block_id, effective in resolved.items() if effective.admits(user)}


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
