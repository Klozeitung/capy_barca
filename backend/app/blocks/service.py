"""
Block service.

Business logic for block operations that require coordination across
multiple repository calls or enforce domain rules. No HTTP concerns live
here – the service operates on domain objects and raises plain Python
exceptions that the router layer translates into HTTP responses.

All public functions accept a SQLAlchemy ``Session`` as their first
argument. The caller (router) is responsible for committing or rolling
back the transaction.
"""
import uuid
from collections import deque
from typing import Final, Optional

from sqlalchemy.orm import Session

from app.blocks import events as ev
from app.blocks import repository as repo
from app.blocks.models import Block, PropertySchema
from app.blocks.types import validate_block_type

# Minimum acceptable gap between adjacent sibling positions. When any gap
# falls below this threshold after a move, the parent's sibling list is
# rebalanced to evenly spaced integers (1.0, 2.0, …). IEEE 754 double
# precision has ~15–16 significant decimal digits; 1e-9 leaves ample room
# before precision loss causes misordering.
_REBALANCE_THRESHOLD: Final[float] = 1e-9


# ─── Exceptions ───────────────────────────────────────────────────────────────


class BlockNotFound(Exception):
    """Raised when a requested block does not exist."""


class BlockConflict(Exception):
    """Raised when an operation would violate a domain invariant."""


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _get_or_raise(db: Session, block_id: uuid.UUID) -> Block:
    """Return block or raise :exc:`BlockNotFound`."""
    try:
        return repo.get_block_or_raise(db, block_id)
    except KeyError:
        raise BlockNotFound(f"Block {block_id} not found")


def _block_snapshot(block: Block) -> dict:
    """Return a compact snapshot dict of the fields relevant for event history."""
    return {
        "type": block.type,
        "parent_id": str(block.parent_id) if block.parent_id else None,
        "position": block.position,
        "state": block.state,
        "icon": block.icon,
        "cover": block.cover,
        "content": block.content,
    }


def _collect_subtree_ids(db: Session, root_id: uuid.UUID) -> list[uuid.UUID]:
    """
    Return the IDs of *root_id* and all its descendants via breadth-first
    traversal.

    This is intentionally done in application code rather than a recursive
    CTE so that the behaviour is identical under SQLite (tests) and
    PostgreSQL (production).

    Parameters
    ----------
    db:
        Active database session.
    root_id:
        UUID of the subtree root.
    """
    ids: list[uuid.UUID] = []
    queue: deque[uuid.UUID] = deque([root_id])
    while queue:
        current = queue.popleft()
        ids.append(current)
        children = repo.list_children(db, current, state=None)
        queue.extend(c.id for c in children)
    return ids


def _min_sibling_gap(siblings: list[Block]) -> float:
    """
    Return the minimum position gap between any two consecutive active
    siblings (sorted by position).

    Returns ``float('inf')`` when there are fewer than two siblings, i.e.
    rebalancing is never needed for a single-child or empty parent.

    Parameters
    ----------
    siblings:
        Active siblings sorted ascending by position, as returned by
        :func:`repo.list_children`.
    """
    if len(siblings) < 2:
        return float("inf")
    positions = [s.position for s in siblings]
    return min(b - a for a, b in zip(positions, positions[1:]))


def _clear_bilateral_relations(db: Session, block: Block) -> set[str]:
    """
    Remove *block*'s ID from the mirror side of every bilateral relation it
    participates in.

    Called immediately before a block leaves a database so that the relation
    graph stays consistent.  Only ``direction == "bilateral"`` and
    ``direction == "bilateral_self"`` schemas are touched; unilateral relations
    are left as-is because they have no mirror.

    Parameters
    ----------
    db:
        Active database session.
    block:
        The entry block that is leaving its current database.

    Returns
    -------
    set[str]
        UUIDs (as strings) of target databases whose mirror values were
        modified.  The caller can broadcast ``database_entries_updated`` for
        each affected database so that open views stay in sync.
        For ``bilateral_self`` relations the target is the block's own
        database, which the caller already broadcasts; these are therefore
        not included in the returned set.
    """
    block_id_str = str(block.id)
    affected_db_ids: set[str] = set()

    for pv in repo.list_values(db, block.id):
        schema = db.get(PropertySchema, pv.property_schema_id)
        if schema is None or schema.type != "relation":
            continue
        config = schema.config or {}
        direction = config.get("direction")
        if direction not in ("bilateral", "bilateral_self"):
            continue
        if not pv.value:
            continue
        related_ids: list[str] = list(pv.value.get("related_ids", []))
        if not related_ids:
            continue

        if direction == "bilateral_self":
            # Mirror is the same schema in the same database.
            mirror_schema = schema
        else:
            target_db_id_raw = config.get("target_database_id")
            if not target_db_id_raw:
                continue
            try:
                target_db_id = uuid.UUID(str(target_db_id_raw))
            except ValueError:
                continue

            mirror_name: str = config.get("mirror_property_name") or schema.name
            mirror_schema = repo.get_schema_by_name(db, target_db_id, mirror_name)
            if mirror_schema is None:
                continue

        for rid_str in related_ids:
            try:
                rid = uuid.UUID(rid_str)
            except ValueError:
                continue
            mirror_pv = repo.get_value(db, rid, mirror_schema.id)
            if mirror_pv and mirror_pv.value:
                current = [
                    i for i in mirror_pv.value.get("related_ids", [])
                    if i != block_id_str
                ]
                repo.upsert_value(
                    db,
                    page_id=rid,
                    schema_id=mirror_schema.id,
                    value={"related_ids": current} if current else None,
                )
                if direction == "bilateral":
                    affected_db_ids.add(str(config.get("target_database_id")))

    return affected_db_ids


def _write_schema_memory(db: Session, block: Block) -> None:
    """
    Snapshot all current PropertyValues and their schemas into
    ``content.schemaMemory`` so the block retains its property data
    while living outside any database.

    Each entry stores enough information to recreate the schema and value
    in any target database on a later move:

    .. code-block:: json

        {
            "schemaMemory": [
                {
                    "name": "Status",
                    "type": "select",
                    "config": {"mode": "single", "options": ["Todo", "Done"]},
                    "position": 1.0,
                    "value": {"option": "Done"}
                }
            ]
        }

    Relation-type properties are excluded from the snapshot because they
    reference external block IDs that are database-bound and not portable.
    Before writing the snapshot, the mirror side of every bilateral relation
    is cleaned up via :func:`_clear_bilateral_relations`.

    Parameters
    ----------
    db:
        Active database session.
    block:
        The block leaving a database. Its ``content`` field is updated
        in place via :func:`repo.update_block`.
    """
    # Before snapshotting, clean up the mirror side of every bilateral
    # relation this block participates in.
    _clear_bilateral_relations(db, block)

    values = repo.list_values(db, block.id)
    memory: list[dict] = []
    for pv in values:
        src_schema = db.get(PropertySchema, pv.property_schema_id)
        if src_schema is None or src_schema.type == "relation":
            continue
        memory.append(
            {
                "name": src_schema.name,
                "type": src_schema.type,
                "config": src_schema.config,
                "position": src_schema.position,
                "value": pv.value,
            }
        )
    content = dict(block.content) if block.content else {}
    content["schemaMemory"] = memory
    repo.update_block(db, block, content=content)


def _migrate_to_database(
    db: Session,
    block: Block,
    target_db_id: uuid.UUID,
) -> None:
    """
    Migrate property values from *block* into *target_db_id*.

    Two sources are supported:

    ``content.schemaMemory`` present
        The block is arriving from a non-database parent.  All orphaned
        live PropertyValues are deleted first; then each memory entry is
        used to find-or-create a schema in the target database by name and
        upsert the stored value.  ``schemaMemory`` is removed from
        ``content`` after a successful migration.

    ``content.schemaMemory`` absent
        Classic database-to-database move.  Live PropertyValues are
        migrated by schema name: existing schemas are re-pointed; missing
        schemas are created in the target (except ``type='relation'``
        which is not portable).

    In both cases, ``schemaMemory`` is cleared from ``content`` once the
    migration completes.

    Parameters
    ----------
    db:
        Active database session.
    block:
        The block entering a database.
    target_db_id:
        UUID of the destination database block.
    """
    content = dict(block.content) if block.content else {}
    schema_memory: list[dict] | None = content.get("schemaMemory")

    if schema_memory is not None:
        # Block is arriving from a non-database parent: use the memory
        # snapshot as source of truth.  Delete orphaned live values first
        # so there are no stale references to schemas in the old database.
        for pv in repo.list_values(db, block.id):
            db.delete(pv)
        db.flush()

        for entry in schema_memory:
            if entry.get("type") == "relation":
                continue
            tgt_schema = repo.get_schema_by_name(db, target_db_id, entry["name"])
            if tgt_schema is None:
                tgt_schema = repo.create_schema(
                    db,
                    database_id=target_db_id,
                    name=entry["name"],
                    type=entry["type"],
                    position=entry["position"],
                    config=entry.get("config"),
                )
            repo.upsert_value(
                db,
                page_id=block.id,
                schema_id=tgt_schema.id,
                value=entry.get("value"),
            )

        content.pop("schemaMemory")
        repo.update_block(db, block, content=content)

    else:
        # Classic database-to-database move: migrate live PropertyValues
        # by schema name.
        values = repo.list_values(db, block.id)
        for pv in values:
            src_schema = db.get(PropertySchema, pv.property_schema_id)
            if src_schema is None or src_schema.type == "relation":
                continue
            tgt_schema = repo.get_schema_by_name(db, target_db_id, src_schema.name)
            if tgt_schema is None:
                tgt_schema = repo.create_schema(
                    db,
                    database_id=target_db_id,
                    name=src_schema.name,
                    type=src_schema.type,
                    position=src_schema.position,
                    config=src_schema.config,
                )
            repo.upsert_value(
                db,
                page_id=block.id,
                schema_id=tgt_schema.id,
                value=pv.value,
            )
            if tgt_schema.id != pv.property_schema_id:
                db.delete(pv)

        # Defensively clear any leftover schemaMemory.
        if "schemaMemory" in content:
            content.pop("schemaMemory")
            repo.update_block(db, block, content=content)


# ─── Position helpers ─────────────────────────────────────────────────────────


def position_after_last(db: Session, parent_id: uuid.UUID) -> float:
    """
    Return a fractional index position that places a new block after all
    existing active siblings under *parent_id*.

    Returns ``1.0`` when *parent_id* has no active children.

    Parameters
    ----------
    db:
        Active database session.
    parent_id:
        UUID of the parent block.
    """
    children = repo.list_children(db, parent_id, state="active")
    if not children:
        return 1.0
    return children[-1].position + 1.0


def position_between(before: float, after: float) -> float:
    """
    Return the midpoint between *before* and *after* for fractional indexing.

    Parameters
    ----------
    before:
        Position of the preceding sibling.
    after:
        Position of the following sibling.
    """
    return (before + after) / 2.0


def rebalance_positions(db: Session, parent_id: uuid.UUID) -> list[uuid.UUID]:
    """
    Normalise the positions of all active children of *parent_id* to evenly
    spaced integers (1.0, 2.0, 3.0, …), preserving their current order.

    This is called automatically by :func:`move` when the minimum gap between
    adjacent siblings falls below :data:`_REBALANCE_THRESHOLD`. It can also
    be triggered manually via the ``POST /api/blocks/{id}/rebalance-children``
    endpoint.

    Parameters
    ----------
    db:
        Active database session.
    parent_id:
        UUID of the parent whose children should be rebalanced.

    Returns
    -------
    list[uuid.UUID]
        IDs of all blocks whose positions were changed. Empty when positions
        were already evenly spaced.
    """
    children = repo.list_children(db, parent_id, state="active")
    changed: list[uuid.UUID] = []
    for idx, block in enumerate(children, start=1):
        new_pos = float(idx)
        if block.position != new_pos:
            repo.update_block(db, block, position=new_pos)
            changed.append(block.id)
    return changed


# ─── Core operations ──────────────────────────────────────────────────────────


def create_block(
    db: Session,
    *,
    type: str,
    parent_id: uuid.UUID,
    position: Optional[float] = None,
    reference_id: Optional[uuid.UUID] = None,
    content: Optional[dict] = None,
    icon: Optional[str] = None,
    cover: Optional[str] = None,
) -> Block:
    """
    Create a new block under *parent_id*.

    If *position* is omitted, the block is appended after all existing
    active siblings.

    Parameters
    ----------
    db:
        Active database session.
    type:
        Block type string. Must be a member of :data:`~app.blocks.types.BLOCK_TYPES`.
    parent_id:
        UUID of the parent block. Must exist.
    position:
        Explicit fractional index position. Auto-computed when ``None``.
    reference_id:
        Source block UUID for reference blocks such as ``database_view``.
    content:
        Type-specific JSONB payload.
    icon:
        Iconify icon string, e.g. ``"mdi:file-document"``.
    cover:
        Cover value: image URL or ``"gradient:..."`` string.

    Raises
    ------
    BlockNotFound
        If *parent_id* does not exist.
    ValueError
        If *type* is not a registered block type.
    """
    validate_block_type(type)
    _get_or_raise(db, parent_id)
    if position is None:
        position = position_after_last(db, parent_id)
    block = repo.create_block(
        db,
        type=type,
        position=position,
        parent_id=parent_id,
        reference_id=reference_id,
        content=content,
        icon=icon,
        cover=cover,
    )
    ev.emit_created(db, block.id, _block_snapshot(block))
    return block


def deep_duplicate(
    db: Session,
    block_id: uuid.UUID,
    *,
    parent_id: uuid.UUID,
    position: float,
) -> Block:
    """
    Recursively duplicate *block_id* and its entire active subtree under
    *parent_id* at *position*.

    The root copy is placed at the given *position*; each child is copied at
    its original position value so the internal order is preserved exactly.
    Only active children (``state='active'``) are included.

    Parameters
    ----------
    db:
        Active database session.
    block_id:
        UUID of the block to duplicate.
    parent_id:
        UUID of the parent block for the new root copy.
    position:
        Fractional index position for the new root copy among its siblings.

    Returns
    -------
    Block
        The newly created root copy.

    Raises
    ------
    BlockNotFound
        If *block_id* or *parent_id* does not exist.
    """
    original = _get_or_raise(db, block_id)
    _get_or_raise(db, parent_id)

    # Duplicating a synched_origin creates a synched_mirror that points back to
    # the origin.  The mirror shares the origin's live children; no subtree copy
    # is performed.
    if original.type == "synched_origin":
        mirror = repo.create_block(
            db,
            type="synched_mirror",
            position=position,
            parent_id=parent_id,
            reference_id=original.id,
            content={},
            icon=original.icon,
            cover=original.cover,
        )
        ev.emit_created(db, mirror.id, _block_snapshot(mirror))
        return mirror

    # Duplicating a synched_mirror creates another mirror pointing to the same
    # origin.  The lock state (content.locked) is copied so the new mirror
    # inherits the same interactivity setting as the source.
    if original.type == "synched_mirror":
        mirror = repo.create_block(
            db,
            type="synched_mirror",
            position=position,
            parent_id=parent_id,
            reference_id=original.reference_id,
            content=dict(original.content) if original.content else {},
            icon=original.icon,
            cover=original.cover,
        )
        ev.emit_created(db, mirror.id, _block_snapshot(mirror))
        return mirror

    new_block = repo.create_block(
        db,
        type=original.type,
        position=position,
        parent_id=parent_id,
        reference_id=original.reference_id,
        content=dict(original.content) if original.content else None,
        icon=original.icon,
        cover=original.cover,
    )
    ev.emit_created(db, new_block.id, _block_snapshot(new_block))

    # Recursively copy all active children, preserving their relative positions.
    for child in repo.list_children(db, block_id, state="active"):
        deep_duplicate(db, child.id, parent_id=new_block.id, position=child.position)

    return new_block


def update_block_appearance(
    db: Session,
    block_id: uuid.UUID,
    *,
    icon: Optional[str] = None,
    cover: Optional[str] = None,
) -> Block:
    """
    Update the ``icon`` and/or ``cover`` of a block, emitting discrete events
    for each changed field.

    Parameters
    ----------
    db:
        Active database session.
    block_id:
        UUID of the block to update.
    icon:
        New Iconify icon string, or ``None`` to leave unchanged.
    cover:
        New cover value, or ``None`` to leave unchanged.

    Raises
    ------
    BlockNotFound
        If *block_id* does not exist.
    """
    block = _get_or_raise(db, block_id)
    before = _block_snapshot(block)

    if icon is not None and icon != block.icon:
        repo.update_block(db, block, icon=icon)
        ev.emit_icon_changed(db, block_id, before["icon"], icon)

    if cover is not None and cover != block.cover:
        repo.update_block(db, block, cover=cover)
        ev.emit_cover_changed(db, block_id, before["cover"], cover)

    return block


def soft_delete(db: Session, block_id: uuid.UUID) -> list[uuid.UUID]:
    """
    Soft-delete *block_id* and all its descendants by setting their state
    to ``'trash'``.

    Only blocks currently in ``state='active'`` are modified; descendants
    already in other states (e.g. already trashed) are left untouched.

    Parameters
    ----------
    db:
        Active database session.
    block_id:
        UUID of the block to soft-delete.

    Returns
    -------
    list[uuid.UUID]
        IDs of all blocks that were transitioned to ``'trash'``.

    Raises
    ------
    BlockNotFound
        If *block_id* does not exist.
    BlockConflict
        If *block_id* refers to the workspace root.
    """
    block = _get_or_raise(db, block_id)
    if block.type == "workspace":
        raise BlockConflict("The workspace root block cannot be deleted.")

    subtree_ids = _collect_subtree_ids(db, block_id)
    changed: list[uuid.UUID] = []
    for bid in subtree_ids:
        b = repo.get_block(db, bid)
        if b is not None and b.state == "active":
            repo.update_block(db, b, state="trash")
            ev.emit_state_changed(db, bid, "active", "trash")
            changed.append(bid)
    return changed


def purge(db: Session, block_id: uuid.UUID) -> set[str]:
    """
    Permanently delete *block_id* and its entire subtree from the database.

    This operation is irreversible. ``ON DELETE CASCADE`` on ``parent_id``
    removes all descendant blocks automatically once the root is deleted.

    Only blocks in ``state='trash'`` may be purged. To hard-delete an
    active block, call :func:`soft_delete` first.

    Before deleting, the mirror side of every bilateral relation that any
    database entry in the subtree participates in is cleaned up so that
    entries in other databases do not retain dangling IDs.  ``ON DELETE
    CASCADE`` removes the purged entries' own ``PropertyValue`` rows, but
    it cannot reach relation values that live in different databases.

    Parameters
    ----------
    db:
        Active database session.
    block_id:
        UUID of the block to permanently delete.

    Returns
    -------
    set[str]
        UUIDs (as strings) of external databases whose mirror relation values
        were modified.  The caller should broadcast
        ``database_entries_updated`` for each so that open views refresh.

    Raises
    ------
    BlockNotFound
        If *block_id* does not exist.
    BlockConflict
        If *block_id* is not in ``state='trash'``, or refers to the
        workspace root.
    """
    block = _get_or_raise(db, block_id)
    if block.type == "workspace":
        raise BlockConflict("The workspace root block cannot be purged.")
    if block.state != "trash":
        raise BlockConflict(
            f"Block {block_id} must be in state 'trash' before purging. "
            "Call soft_delete first."
        )

    # Clean up bilateral relation mirrors for every database entry in the
    # subtree.  ON DELETE CASCADE removes the entries' own PropertyValues,
    # but references to their IDs in *other* databases are out of reach of
    # CASCADE and must be removed explicitly before the delete.
    affected_db_ids: set[str] = set()
    for bid in _collect_subtree_ids(db, block_id):
        b = repo.get_block(db, bid)
        if b is None or not b.parent_id:
            continue
        parent = repo.get_block(db, b.parent_id)
        if parent is not None and parent.type == "database":
            affected_db_ids |= _clear_bilateral_relations(db, b)

    db.delete(block)
    db.flush()
    return affected_db_ids


def restore(db: Session, block_id: uuid.UUID) -> list[uuid.UUID]:
    """
    Restore *block_id* and all its descendants from ``trash`` to ``active``.

    Only blocks currently in ``state='trash'`` are modified; descendants
    already in other states are left untouched.

    Parameters
    ----------
    db:
        Active database session.
    block_id:
        UUID of the block to restore.

    Returns
    -------
    list[uuid.UUID]
        IDs of all blocks that were transitioned back to ``active``.

    Raises
    ------
    BlockNotFound
        If *block_id* does not exist.
    BlockConflict
        If *block_id* is not currently in ``state='trash'``.
    """
    block = _get_or_raise(db, block_id)
    if block.state != "trash":
        raise BlockConflict(f"Block {block_id} is not in state 'trash'.")

    affected_ids = _collect_subtree_ids(db, block_id)
    restored: list[uuid.UUID] = []
    for bid in affected_ids:
        b = repo.get_block(db, bid)
        if b is not None and b.state == "trash":
            repo.update_block(db, b, state="active")
            ev.emit_state_changed(db, bid, "trash", "active")
            restored.append(bid)
    return restored


def update_block_fields(
    db: Session,
    block_id: uuid.UUID,
    *,
    type: Optional[str] = None,
    content: Optional[dict] = None,
    position: Optional[float] = None,
    state: Optional[str] = None,
) -> Block:
    """
    Update mutable fields on *block_id* and emit a ``content_updated`` audit event.

    Parameters
    ----------
    db:
        Active database session.
    block_id:
        UUID of the block to update.
    type, content, position, state:
        Fields to update. ``None`` means "do not change".

    Raises
    ------
    BlockNotFound
        If *block_id* does not exist.
    """
    block = _get_or_raise(db, block_id)
    before = _block_snapshot(block)
    repo.update_block(db, block, type=type, content=content, position=position, state=state)
    ev.emit_block_updated(db, block_id, before, _block_snapshot(block))
    return block


def move(
    db: Session,
    block_id: uuid.UUID,
    *,
    new_parent_id: uuid.UUID,
    new_position: float,
) -> Block:
    """
    Move *block_id* to *new_parent_id* at *new_position*.

    Property value handling
    -----------------------
    Blocks carry their property data across all parent transitions:

    ``database → non-database``
        The mirror side of every bilateral relation the block participates
        in is cleared first.  Then a snapshot of all non-relation
        PropertyValues and their schemas is written to
        ``content.schemaMemory``.  The live PropertyValue rows are left in
        place so no data is lost should the move be undone at the DB level.
        Relation-type properties are excluded from the snapshot as they are
        database-bound.

    ``non-database → database``
        ``content.schemaMemory`` is read and each entry is used to
        find-or-create a schema in the target database by name, then
        upsert the stored value.  Orphaned live PropertyValue rows from
        the previous database are deleted beforehand.  ``schemaMemory``
        is removed from ``content`` once migration succeeds.

    ``database → database`` (different databases)
        Live PropertyValues are migrated by schema name: existing schemas
        in the target are re-pointed; missing schemas are created (except
        ``type='relation'``).  Any leftover ``schemaMemory`` is cleared.

    After the move, if the minimum position gap among the new parent's active
    siblings falls below :data:`_REBALANCE_THRESHOLD`, the sibling list is
    automatically rebalanced to evenly spaced integers to prevent future
    precision exhaustion.

    Parameters
    ----------
    db:
        Active database session.
    block_id:
        UUID of the block to move.
    new_parent_id:
        UUID of the destination parent block.
    new_position:
        Fractional index position in the destination parent.

    Raises
    ------
    BlockNotFound
        If *block_id* or *new_parent_id* does not exist.
    BlockConflict
        If *block_id* is the workspace root, if moving the block would
        create a cycle (i.e. *new_parent_id* is a descendant of *block_id*),
        or if a non-page/non-workspace block is moved under a non-page parent.
    """
    block = _get_or_raise(db, block_id)
    new_parent = _get_or_raise(db, new_parent_id)

    if block.type == "workspace":
        raise BlockConflict("The workspace root block cannot be moved.")

    # Parent type guard: content blocks (non-page, non-workspace) may not be
    # placed directly under workspace or database blocks. They may live under
    # any content-container type such as page, toggle, callout, quote, etc.
    _FORBIDDEN_CONTENT_PARENTS = {"workspace", "database"}
    if block.type not in ("page", "workspace") and new_parent.type in _FORBIDDEN_CONTENT_PARENTS:
        raise BlockConflict(
            f"Block of type '{block.type}' cannot be moved under a "
            f"'{new_parent.type}' block."
        )

    # Cycle guard: new_parent_id must not be in the subtree rooted at block_id.
    subtree_ids = _collect_subtree_ids(db, block_id)
    if new_parent_id in subtree_ids:
        raise BlockConflict(
            f"Cannot move block {block_id} into its own descendant {new_parent_id}."
        )

    # Capture before-state for event emission.
    old_parent_id = block.parent_id
    before_position = block.position

    # ── Property value migration ───────────────────────────────────────────────
    old_parent = repo.get_block(db, old_parent_id) if old_parent_id else None
    old_is_db = old_parent is not None and old_parent.type == "database"
    new_is_db = new_parent.type == "database"

    if old_is_db and not new_is_db:
        # DB → non-DB: persist schema+value snapshot in the block itself.
        _write_schema_memory(db, block)
    elif new_is_db and old_parent_id != new_parent_id:
        # non-DB → DB or DB → DB (cross-database): migrate into target database.
        _migrate_to_database(db, block, new_parent_id)

    # ── Apply the move ────────────────────────────────────────────────────────
    repo.update_block(db, block, parent_id=new_parent_id, position=new_position)
    db.flush()

    # emit_moved signature:
    #   (db, block_id, before_parent_id, after_parent_id,
    #    before_position, after_position)
    ev.emit_moved(
        db,
        block_id,
        old_parent_id,
        new_parent_id,
        before_position,
        new_position,
    )

    # ── Auto-rebalance if gap has become critically small ─────────────────────
    siblings = repo.list_children(db, new_parent_id, state="active")
    if _min_sibling_gap(siblings) < _REBALANCE_THRESHOLD:
        rebalance_positions(db, new_parent_id)

    db.refresh(block)
    return block
