"""
Synchronisation and mode rules of relation properties.

Mirrors
-------
When a ``relation`` property has ``config.direction == "bilateral"``, a
mirror property in the target database holds the back-references; with
``"bilateral_self"`` the property mirrors itself. Writing a value keeps the
mirror side in sync within the same transaction
(:func:`sync_bilateral_relation`, :func:`sync_bilateral_relation_timeline`).

Sub-item hierarchy
------------------
Every database has a linked ``parent_item`` / ``sub_item`` pair. Writing
``parent_item`` updates the ``sub_item`` lists of the old and new parent
(:func:`sync_parent_item`, single-parent policy, with a cycle check in
:func:`would_create_parent_cycle`); writing ``sub_item`` directly updates the
``parent_item`` of each affected child (:func:`sync_sub_item`).

Relation modes
--------------
A relation is vanilla, timelined, nuanced, timelined and nuanced, or keyed;
keying combines with nothing (:func:`validate_relation_mode_exclusivity`), and
a keyed side restricts the other side of a bilateral relation to vanilla or
keyed (:func:`assert_relation_cross_side_lock`). Keying is a read-side
pointer into the target database: it never participates in synchronisation,
and deleting or retyping the key property resets every relation keyed on it
(:func:`clear_keying_for_property`).

All functions run inside the caller's transaction and never commit.
"""
import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.blocks import repository as repo
from app.blocks.timeline import extract_related_ids_now, pool_to_timeline


# ─── Mirrors of bilateral relations ───────────────────────────────────────────


def ensure_bilateral_mirror(
    db: Session,
    source_schema,
    target_db_id: uuid.UUID,
    mirror_name: str,
) -> bool:
    """
    Ensure the mirror schema for a bilateral relation exists in *target_db_id*.

    Creates the mirror schema if it is absent.  Safe to call multiple times —
    a second call is a no-op when the schema already exists.

    The mirror schema itself is a bilateral relation pointing back at the source
    database, so both sides know about each other from the moment of creation.

    Parameters
    ----------
    db:
        Active database session.
    source_schema:
        The ``PropertySchema`` on the source side (type ``relation``,
        direction ``bilateral``).
    target_db_id:
        UUID of the database that should host the mirror property.
    mirror_name:
        Name to give the mirror schema (= ``config.mirror_property_name``).

    Returns
    -------
    bool
        ``True`` if a new mirror schema was created, ``False`` if it already
        existed.  The caller uses this to decide whether to broadcast a
        ``database_schema_updated`` event to clients of the target database.
    """
    existing = repo.get_schema_by_name(db, target_db_id, mirror_name)
    if existing is not None:
        return False  # already present — nothing to do

    existing_schemas = repo.list_schemas(db, target_db_id)
    pos = existing_schemas[-1].position + 1.0 if existing_schemas else 1.0
    repo.create_schema(
        db,
        database_id=target_db_id,
        name=mirror_name,
        type="relation",
        position=pos,
        config={
            "target_database_id": str(source_schema.database_id),
            "direction": "bilateral",
            "mirror_property_name": source_schema.name,
        },
    )
    return True


def get_mirror_schema(db: Session, schema) -> Optional[object]:
    """
    Resolve the mirror ``PropertySchema`` for a bilateral relation.

    Returns ``None`` for unilateral relations or when the mirror cannot be found.
    """
    config = schema.config or {}
    direction = config.get("direction")
    if direction == "bilateral_self":
        return schema
    if direction != "bilateral":
        return None
    target_db_id_raw = config.get("target_database_id")
    if not target_db_id_raw:
        return None
    try:
        target_db_id = uuid.UUID(str(target_db_id_raw))
    except ValueError:
        return None
    mirror_name: str = config.get("mirror_property_name") or schema.name
    return repo.get_schema_by_name(db, target_db_id, mirror_name)


def sync_bilateral_relation(
    db: Session,
    schema,
    entry_id: uuid.UUID,
    new_related_ids: list[str],
    old_related_ids: list[str],
    new_nuances: Optional[dict] = None,
) -> set[uuid.UUID]:
    """
    Maintain the mirror side of a bilateral relation property.

    Returns the IDs of the entries whose mirror value was written.

    Parameters
    ----------
    db:
        Active database session (within the caller's transaction).
    schema:
        The source ``PropertySchema`` with ``type == 'relation'`` and
        ``config.direction`` one of ``'bilateral'`` or ``'bilateral_self'``.
    entry_id:
        UUID of the entry whose relation value was just written.
    new_related_ids:
        The ``related_ids`` list that was just stored (strings).
    old_related_ids:
        The ``related_ids`` list that existed *before* this write (strings).
    new_nuances:
        Optional ``{ uid: label }`` map from the source value.  The nuance is a
        shared property of the pairing, so the label the source recorded for a
        target is mirrored onto the target's value for *entry_id*.  Per-side
        framing (affixes / orientation) lives in each schema's config and is
        applied at render time, not stored here.
    """
    config = schema.config or {}
    direction = config.get("direction")
    src_nuances: dict = new_nuances or {}
    written: set[uuid.UUID] = set()

    # bilateral_self: the mirror IS this schema — entries of the same property
    # in the same database point back at entry_id symmetrically.
    if direction == "bilateral_self":
        mirror_schema = schema
    else:
        target_db_id_raw = config.get("target_database_id")
        if not target_db_id_raw:
            return written

        try:
            target_db_id = uuid.UUID(str(target_db_id_raw))
        except ValueError:
            return written

        mirror_name: str = (
            config.get("mirror_property_name") or schema.name
        )

        # Ensure the mirror schema exists in the target database.
        mirror_schema = repo.get_schema_by_name(db, target_db_id, mirror_name)
        if mirror_schema is None:
            ensure_bilateral_mirror(db, schema, target_db_id, mirror_name)
            mirror_schema = repo.get_schema_by_name(db, target_db_id, mirror_name)
        if mirror_schema is None:
            return written

    added = set(new_related_ids) - set(old_related_ids)
    removed = set(old_related_ids) - set(new_related_ids)
    entry_id_str = str(entry_id)

    for rid_str in added:
        try:
            rid = uuid.UUID(rid_str)
        except ValueError:
            continue
        pv = repo.get_value(db, rid, mirror_schema.id)
        current: list[str] = []
        current_nuances: dict = {}
        if pv and pv.value:
            current = list(extract_related_ids_now(pv.value))
            current_nuances = dict(pv.value.get("nuances") or {})
        if entry_id_str not in current:
            current.append(entry_id_str)
        label = src_nuances.get(rid_str)
        if label:
            current_nuances[entry_id_str] = label
        else:
            current_nuances.pop(entry_id_str, None)
        # Keep only nuances whose uid is still linked.
        current_nuances = {u: l for u, l in current_nuances.items() if u in current}
        value: dict = {"related_ids": current}
        if current_nuances:
            value["nuances"] = current_nuances
        repo.upsert_value(
            db,
            page_id=rid,
            schema_id=mirror_schema.id,
            value=value,
        )
        written.add(rid)

    for rid_str in removed:
        try:
            rid = uuid.UUID(rid_str)
        except ValueError:
            continue
        pv = repo.get_value(db, rid, mirror_schema.id)
        if pv and pv.value:
            current = [
                i for i in extract_related_ids_now(pv.value) if i != entry_id_str
            ]
            current_nuances = {
                u: l for u, l in (pv.value.get("nuances") or {}).items()
                if u in current
            }
            if current:
                value = {"related_ids": current}
                if current_nuances:
                    value["nuances"] = current_nuances
                repo.upsert_value(
                    db, page_id=rid, schema_id=mirror_schema.id, value=value,
                )
            else:
                repo.upsert_value(
                    db, page_id=rid, schema_id=mirror_schema.id, value=None,
                )
            written.add(rid)

    return written


def sync_bilateral_relation_timeline(
    db: Session,
    schema,
    mirror_schema,
    entry_id: uuid.UUID,
    new_pool: dict,
    old_pool: dict,
    new_nuance_pool: Optional[dict] = None,
) -> set[uuid.UUID]:
    """
    Synchronise the mirror side of a bilateral timeline relation.

    Returns the IDs of the entries whose mirror value was written.

    Computes the diff between *old_pool* and *new_pool* (per UUID × range
    pairs), and for each added/removed pair updates the mirror entry's pool
    accordingly.  After each pool change the mirror's ``_timeline`` is
    recomputed via :func:`pool_to_timeline`.

    Parameters
    ----------
    db:
        Active SQLAlchemy session (within the caller's transaction).
    schema:
        The source ``PropertySchema`` (type ``'relation'``, timeline-enabled).
    mirror_schema:
        The resolved mirror schema in the target database.
    entry_id:
        UUID of the entry being written.
    new_pool:
        The updated ``relationPool`` dict just sent by the client.
    old_pool:
        The ``relationPool`` dict that existed before this write.
    new_nuance_pool:
        Optional ``{ uid: { range_str: label } }`` map from the source value.
        For each mirrored (uid, range) pair the source's label is copied onto
        the mirror's ``nuancePool`` under ``{ entry_id: { range_str: label } }``
        — the nuance value is shared; only per-side framing differs and that is
        applied at render time from each schema's config.
    """
    written: set[uuid.UUID] = set()
    if mirror_schema is None:
        return written

    entry_id_str = str(entry_id)
    src_nuance: dict = new_nuance_pool or {}

    # Build flat sets of (uid, range_str) pairs for diff
    old_pairs: set[tuple[str, str]] = {
        (uid, r) for uid, ranges in old_pool.items() for r in (ranges or [])
    }
    new_pairs: set[tuple[str, str]] = {
        (uid, r) for uid, ranges in new_pool.items() for r in (ranges or [])
    }
    added = new_pairs - old_pairs
    removed = old_pairs - new_pairs

    for uid_str, range_str in added:
        try:
            uid = uuid.UUID(uid_str)
        except ValueError:
            continue
        pv = repo.get_value(db, uid, mirror_schema.id)
        current_value = (pv.value if pv else None) or {}
        current_pool = dict(current_value.get("relationPool") or {})
        current_nuance = {
            k: dict(v) for k, v in (current_value.get("nuancePool") or {}).items()
        }
        uid_ranges: list[str] = list(current_pool.get(entry_id_str) or [])
        if range_str not in uid_ranges:
            uid_ranges.append(range_str)
        current_pool[entry_id_str] = uid_ranges
        # Mirror the source label for this (uid, range) onto the mirror pair.
        label = (src_nuance.get(uid_str) or {}).get(range_str)
        if label:
            current_nuance.setdefault(entry_id_str, {})[range_str] = label
        new_timeline = pool_to_timeline(current_pool, current_nuance)
        value: dict = {"relationPool": current_pool}
        if current_nuance:
            value["nuancePool"] = current_nuance
        value["_timeline"] = new_timeline
        repo.upsert_value(db, page_id=uid, schema_id=mirror_schema.id, value=value)
        written.add(uid)

    for uid_str, range_str in removed:
        try:
            uid = uuid.UUID(uid_str)
        except ValueError:
            continue
        pv = repo.get_value(db, uid, mirror_schema.id)
        if not pv or not pv.value:
            continue
        current_pool = dict(pv.value.get("relationPool") or {})
        current_nuance = {
            k: dict(v) for k, v in (pv.value.get("nuancePool") or {}).items()
        }
        uid_ranges = [r for r in (current_pool.get(entry_id_str) or []) if r != range_str]
        if uid_ranges:
            current_pool[entry_id_str] = uid_ranges
        else:
            current_pool.pop(entry_id_str, None)
        # Drop the mirrored nuance for the removed (entry_id, range) pair.
        if entry_id_str in current_nuance:
            current_nuance[entry_id_str].pop(range_str, None)
            if not current_nuance[entry_id_str]:
                current_nuance.pop(entry_id_str, None)
        if current_pool:
            new_timeline = pool_to_timeline(current_pool, current_nuance)
            value = {"relationPool": current_pool}
            if current_nuance:
                value["nuancePool"] = current_nuance
            value["_timeline"] = new_timeline
            repo.upsert_value(db, page_id=uid, schema_id=mirror_schema.id, value=value)
        else:
            repo.upsert_value(db, page_id=uid, schema_id=mirror_schema.id, value=None)
        written.add(uid)

    return written


# ─── Sub-item hierarchy ───────────────────────────────────────────────────────


def sync_parent_item(
    db: Session,
    entry_id: uuid.UUID,
    new_parent_ids: list[str],
    old_parent_ids: list[str],
    sub_item_schema,
) -> set[uuid.UUID]:
    """
    Maintain the ``sub_item`` mirror whenever a ``parent_item`` value is written.

    When entry A sets its ``parent_item`` to entry B:
    * B's ``sub_item`` gains A.
    * A's previous parent (if any) has A removed from its ``sub_item``.

    This enforces the single-parent invariant: ``new_parent_ids`` must contain
    at most one element (validated before this call).

    Parameters
    ----------
    db:
        Active database session (within the caller's transaction).
    entry_id:
        UUID of the entry whose ``parent_item`` was just written.
    new_parent_ids:
        The ``related_ids`` list that was just stored (0 or 1 element).
    old_parent_ids:
        The ``related_ids`` list that existed before this write.
    sub_item_schema:
        The partner ``PropertySchema`` of type ``sub_item`` in the same
        database, resolved via ``config.partner_schema_id``.  If ``None``
        the sync is a no-op (schema pair not yet set up).

    Returns the IDs of the entries whose ``sub_item`` value was written.
    """
    written: set[uuid.UUID] = set()
    if sub_item_schema is None:
        return written

    entry_id_str = str(entry_id)
    added   = set(new_parent_ids) - set(old_parent_ids)
    removed = set(old_parent_ids) - set(new_parent_ids)

    # Remove this entry from the old parent's sub_item list.
    for rid_str in removed:
        try:
            rid = uuid.UUID(rid_str)
        except ValueError:
            continue
        pv = repo.get_value(db, rid, sub_item_schema.id)
        if pv and pv.value:
            current = [i for i in (pv.value.get("related_ids") or []) if i != entry_id_str]
            repo.upsert_value(
                db,
                page_id=rid,
                schema_id=sub_item_schema.id,
                value={"related_ids": current} if current else None,
            )
            written.add(rid)

    # Add this entry to the new parent's sub_item list.
    for rid_str in added:
        try:
            rid = uuid.UUID(rid_str)
        except ValueError:
            continue
        pv = repo.get_value(db, rid, sub_item_schema.id)
        current: list[str] = []
        if pv and pv.value:
            current = list(pv.value.get("related_ids") or [])
        if entry_id_str not in current:
            current.append(entry_id_str)
        repo.upsert_value(
            db,
            page_id=rid,
            schema_id=sub_item_schema.id,
            value={"related_ids": current},
        )
        written.add(rid)

    return written


def would_create_parent_cycle(
    db: Session,
    entry_id: uuid.UUID,
    proposed_parent_id: uuid.UUID,
    parent_item_schema_id: uuid.UUID,
) -> bool:
    """
    Return True if setting *proposed_parent_id* as the parent of *entry_id*
    would create a cycle in the parent–child hierarchy.

    Walks the ancestor chain of *proposed_parent_id* upward until either
    *entry_id* is found (cycle) or the chain terminates (no cycle).  Bounded
    by a depth limit as a safety net against corrupt existing data.
    """
    MAX_DEPTH = 200
    current_id: uuid.UUID | None = proposed_parent_id
    visited: set[str] = set()
    depth = 0

    while current_id is not None and depth < MAX_DEPTH:
        id_str = str(current_id)
        if id_str in visited:
            break  # Already-broken cycle in existing data — stop safely.
        if current_id == entry_id:
            return True
        visited.add(id_str)
        pv = repo.get_value(db, current_id, parent_item_schema_id)
        if pv is None or not pv.value:
            break
        parent_ids: list[str] = list(pv.value.get("related_ids") or [])
        if not parent_ids:
            break
        try:
            current_id = uuid.UUID(parent_ids[0])
        except ValueError:
            break
        depth += 1

    return False


def sync_sub_item(
    db: Session,
    entry_id: uuid.UUID,
    new_child_ids: list[str],
    old_child_ids: list[str],
    parent_item_schema,
    sub_item_schema_id: uuid.UUID,
) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """
    Maintain the ``parent_item`` mirror whenever a ``sub_item`` value is written
    directly.

    When entry B's sub_item gains child A:
    * A's ``parent_item`` is set to B.
    * If A had a different parent C, A is removed from C's ``sub_item`` list
      (single-parent policy), as :func:`sync_parent_item` does when A is
      reparented through its own ``parent_item``.

    When entry B's sub_item loses child A:
    * A's ``parent_item`` is cleared if it currently points to B.

    Parameters
    ----------
    db:
        Active database session (within the caller's transaction).
    entry_id:
        UUID of the entry whose ``sub_item`` was just written (the parent).
    new_child_ids:
        The ``related_ids`` list that was just stored.
    old_child_ids:
        The ``related_ids`` list that existed before this write.
    parent_item_schema:
        The partner ``PropertySchema`` of type ``parent_item``.
        If ``None`` the sync is a no-op.
    sub_item_schema_id:
        The ``sub_item`` schema that was written. A child's previous parent
        loses the child from its value of this schema.

    Returns two sets of entry IDs: the children whose ``parent_item`` value
    was written, and the previous parents whose ``sub_item`` value was
    written.
    """
    written: set[uuid.UUID] = set()
    previous_parents: set[uuid.UUID] = set()
    if parent_item_schema is None:
        return written, previous_parents

    entry_id_str = str(entry_id)
    added   = set(new_child_ids) - set(old_child_ids)
    removed = set(old_child_ids) - set(new_child_ids)

    # For newly added children: set their parent_item to this entry, and
    # take them off the sub_item list of their previous parent (single-parent
    # policy).
    for cid_str in added:
        try:
            cid = uuid.UUID(cid_str)
        except ValueError:
            continue
        previous = repo.get_value(db, cid, parent_item_schema.id)
        previous_ids = ((previous.value or {}).get("related_ids") or []) if previous else []
        for pid_str in previous_ids:
            if pid_str == entry_id_str:
                continue
            try:
                pid = uuid.UUID(pid_str)
            except ValueError:
                continue
            previous_list = repo.get_value(db, pid, sub_item_schema_id)
            if previous_list is None or not previous_list.value:
                continue
            remaining = [
                i for i in (previous_list.value.get("related_ids") or []) if i != cid_str
            ]
            repo.upsert_value(
                db,
                page_id=pid,
                schema_id=sub_item_schema_id,
                value={"related_ids": remaining} if remaining else None,
            )
            previous_parents.add(pid)
        repo.upsert_value(
            db,
            page_id=cid,
            schema_id=parent_item_schema.id,
            value={"related_ids": [entry_id_str]},
        )
        written.add(cid)

    # For removed children: clear their parent_item if it still points here.
    for cid_str in removed:
        try:
            cid = uuid.UUID(cid_str)
        except ValueError:
            continue
        pv = repo.get_value(db, cid, parent_item_schema.id)
        if pv and pv.value:
            current_parents = list(pv.value.get("related_ids") or [])
            if entry_id_str in current_parents:
                repo.upsert_value(
                    db,
                    page_id=cid,
                    schema_id=parent_item_schema.id,
                    value=None,
                )
                written.add(cid)

    return written, previous_parents


# ─── Relation modes and keying ────────────────────────────────────────────────


# Property types a relation may be keyed on.  Each has a total order that the
# frontend comparator can apply: dates and numbers naturally, select by option
# order rather than label, text lexicographically.  A property retyped out of
# this set drops the keying config of every relation referring to it.
KEYABLE_PROPERTY_TYPES: frozenset[str] = frozenset(
    {
        "date",
        "number",
        "select",
        "text",
    }
)


def is_keying_enabled(config: Optional[dict]) -> bool:
    """True when *config* carries an active keying block."""
    keying = (config or {}).get("keying")
    return isinstance(keying, dict) and keying.get("enabled") is True


def has_nuance_enabled(config: Optional[dict]) -> bool:
    """True when *config* carries an active nuance block."""
    nuance = (config or {}).get("nuance")
    return isinstance(nuance, dict) and nuance.get("enabled") is True


def validate_relation_mode_exclusivity(config: Optional[dict]) -> None:
    """
    Enforce the relation mode matrix.

    A relation property is exactly one of:

        vanilla | timelined | nuanced | timelined + nuanced | keyed

    Keying combines with nothing.

    This is a deliberate scope constraint, not a data-model limitation.  The
    combination is representable and nothing in the storage format would break;
    it is withheld because no established use case exists for it yet, and an
    unexercised combination is an untestable surface — edge cases nobody
    triggers on purpose, bugs nobody can reproduce on demand.  Keeping the
    reachable state space small enough to actually verify is worth more here
    than the extra freedom.  Revisit when a concrete use case exists: this
    check is the only thing gating it.
    """
    if not is_keying_enabled(config):
        return
    cfg = config or {}
    if cfg.get("hasTimeline") is True:
        raise HTTPException(
            status_code=422,
            detail="A keyed relation cannot also be timelined",
        )
    if has_nuance_enabled(cfg):
        raise HTTPException(
            status_code=422,
            detail="A keyed relation cannot also be nuanced",
        )


def assert_relation_cross_side_lock(db: Session, schema, effective_config: Optional[dict]) -> None:
    """
    Enforce the mode lock across the two sides of a bilateral relation.

    Timeline and nuance data live on the edge and are therefore inherently
    bilateral: both sides read the same slots and the same per-link labels.
    Keying does not — it is a read-side pointer into the target database, so
    the sides configure it independently, and two keyed sides pointing at
    different properties are explicitly valid.

    The lock is the consequence of that asymmetry.  A keyed side renders its
    key value in the left zone of the cell and has nowhere to put edge data, so
    the opposite side of a keyed relation is restricted to vanilla or keyed;
    turning it timelined or nuanced is rejected.

    Unilateral relations have no opposite side and are unaffected.  For
    ``bilateral_self`` the mirror is the schema itself, which the local
    exclusivity check already covers.
    """
    mirror = get_mirror_schema(db, schema)
    if mirror is None or mirror.id == schema.id:
        return

    mirror_config = mirror.config or {}
    cfg = effective_config or {}

    wants_timeline = cfg.get("hasTimeline") is True
    wants_nuance = has_nuance_enabled(cfg)

    if is_keying_enabled(mirror_config) and (wants_timeline or wants_nuance):
        raise HTTPException(
            status_code=422,
            detail=(
                f"The mirror property '{mirror.name}' is keyed. A keyed relation "
                f"restricts the other side to vanilla or keyed."
            ),
        )

    if is_keying_enabled(cfg) and (
        mirror_config.get("hasTimeline") is True or has_nuance_enabled(mirror_config)
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"The mirror property '{mirror.name}' is timelined or nuanced. "
                f"Keying requires the other side to be vanilla or keyed."
            ),
        )


def clear_keying_for_property(db: Session, key_property_id: uuid.UUID) -> list:
    """
    Reset every relation that keys on *key_property_id* back to vanilla and
    return the affected schemas.

    Called when the referenced property is deleted or retyped out of the
    keyable set.  Clearing is an actual schema write rather than a display-time
    fallback: a config pointing at something that no longer exists would
    otherwise survive in the database and reappear in the settings modal as a
    blank key property.  The cell keeps its own runtime fallback for races and
    legacy data.
    """
    affected = repo.list_relation_schemas_by_key_property(db, key_property_id)
    for referrer in affected:
        referrer_config = dict(referrer.config or {})
        referrer_config["keying"] = {"enabled": False}
        repo.update_schema(db, referrer, config=referrer_config)
    return affected
