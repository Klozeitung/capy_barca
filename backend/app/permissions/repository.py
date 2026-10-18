"""
Permission repository.

All database access for the permission layer lives here.  No HTTP concerns;
the router layer translates exceptions into HTTP responses.
"""
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Union

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.permissions.model import LEVELS, WRITE, BlockPermission, BlockPermissionGrant
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


def satisfies(granted: Optional[str], required: str) -> bool:
    """
    Return True if access at level *granted* allows an action needing *required*.

    Write access includes read access. ``None`` (no access) satisfies nothing.
    """
    if required not in LEVELS:
        raise ValueError(f"Unknown access level: {required!r}")
    if granted is None:
        return False
    return LEVELS.index(granted) >= LEVELS.index(required)


# ─── Effective permission resolution ─────────────────────────────────────────


@dataclass(frozen=True)
class EffectivePermission:
    """
    The permission that governs a block, after inheritance is resolved.

    ``owner_id`` is the owner of the block that carries the explicit row, which
    is not necessarily the block the question was asked about. ``grants`` keeps
    the order of the stored grant rows; ``grant_levels`` maps each of them to
    its access level. ``everyone_level`` applies while the mode is 'everyone'.

    The value does not depend on who asks, so one instance answers the access
    question for any number of users (see :meth:`level_for`).
    """

    mode: str
    owner_id: Optional[uuid.UUID]
    grants: tuple[uuid.UUID, ...]
    everyone_level: str = WRITE
    grant_levels: Mapping[uuid.UUID, str] = field(default_factory=dict)

    def level_for(self, user: User) -> Optional[str]:
        """
        Return the access level *user* has to a block governed by this
        permission: ``"write"``, ``"read"`` or ``None`` for no access.

        Admins and the owner always have write access.
        """
        if user.role == "admin":
            return WRITE
        if self.owner_id is not None and user.id == self.owner_id:
            return WRITE
        if self.mode == "everyone":
            return self.everyone_level
        if self.mode == "private":
            return None
        if self.mode == "whitelist":
            if user.id in self.grants:
                return self.grant_levels.get(user.id, WRITE)
            return None
        # Residual fallback (shouldn't be reached in practice — the workspace
        # root row always anchors the walk with mode='everyone').
        return WRITE

    def admits(self, user: User, level: str) -> bool:
        """Return True if *user* has at least *level* access ("read" or "write")."""
        return satisfies(self.level_for(user), level)


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
                    everyone_level=perm.everyone_level,
                    grant_levels={g.user_id: g.level for g in perm.grants},
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


def access_level(db: Session, block_id: uuid.UUID, user: User) -> Optional[str]:
    """
    Return the access level *user* has to *block_id*: ``"write"``, ``"read"``
    or ``None``.

    Admin users have write access everywhere without touching the database.
    """
    if user.role == "admin":
        return WRITE
    return resolve_effective_permissions(db, [block_id])[block_id].level_for(user)


def access_levels(
    db: Session, block_ids: Iterable[uuid.UUID], user: User
) -> dict[uuid.UUID, Optional[str]]:
    """
    Return the access level of *user* for each of *block_ids* in one batched
    walk (see :func:`access_level`).
    """
    ids = list(dict.fromkeys(block_ids))
    if user.role == "admin":
        return {block_id: WRITE for block_id in ids}
    resolved = resolve_effective_permissions(db, ids)
    return {block_id: effective.level_for(user) for block_id, effective in resolved.items()}


def can_user_access(
    db: Session, block_id: uuid.UUID, user: User, *, level: str
) -> bool:
    """
    Return True if *user* has at least *level* access to *block_id*.

    *level* is required so every caller states whether it reads or writes.
    Admin users bypass all permission checks without touching the database.
    For non-admins the effective permission is resolved via
    :func:`resolve_effective_permissions`.
    """
    return satisfies(access_level(db, block_id, user), level)


def filter_accessible(
    db: Session, block_ids: Iterable[uuid.UUID], user: User, *, level: str
) -> set[uuid.UUID]:
    """
    Return the subset of *block_ids* that *user* has at least *level* access to.

    Resolves all of them in one batched walk instead of one walk per block.
    Admins receive the full set without a query.
    """
    if level not in LEVELS:
        raise ValueError(f"Unknown access level: {level!r}")
    ids = set(block_ids)
    if user.role == "admin" or not ids:
        return ids
    resolved = resolve_effective_permissions(db, ids)
    return {
        block_id for block_id, effective in resolved.items()
        if effective.admits(user, level)
    }


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
    grants: Union[Iterable[uuid.UUID], Mapping[uuid.UUID, str]],
    *,
    everyone_level: str = WRITE,
) -> BlockPermission:
    """
    Upsert the permission row for *block_id* and replace the grant list.

    *grants* is either a mapping of user id to access level or a plain
    collection of user ids, which then all receive write access.
    *everyone_level* is the level every account gets while *mode* is
    'everyone'.

    Existing grants not present in *grants* are deleted, kept ones take the
    new level, new ones are inserted. The caller must commit after this call.
    """
    if everyone_level not in LEVELS:
        raise ValueError(f"Unknown access level: {everyone_level!r}")
    if isinstance(grants, Mapping):
        wanted: dict[uuid.UUID, str] = dict(grants)
    else:
        wanted = {user_id: WRITE for user_id in grants}
    for level in wanted.values():
        if level not in LEVELS:
            raise ValueError(f"Unknown access level: {level!r}")

    perm = get_permission_row(db, block_id)
    if perm is None:
        perm = BlockPermission(block_id=block_id, mode=mode, everyone_level=everyone_level)
        db.add(perm)
    else:
        perm.mode = mode
        perm.everyone_level = everyone_level

    db.flush()

    # Replace grants atomically.
    existing = (
        db.query(BlockPermissionGrant)
        .filter(BlockPermissionGrant.block_id == block_id)
        .all()
    )
    existing_user_ids = {g.user_id for g in existing}

    for grant in existing:
        if grant.user_id not in wanted:
            db.delete(grant)
        else:
            grant.level = wanted[grant.user_id]
    for uid in wanted.keys() - existing_user_ids:
        db.add(BlockPermissionGrant(block_id=block_id, user_id=uid, level=wanted[uid]))

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
