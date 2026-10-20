"""
Entry value operations behind the entry endpoints.

The endpoints in ``app.blocks.database_entries_router`` check access, take
the relation locks, run these steps inside one transaction, commit and
broadcast. Nothing here commits.

Writing a value
---------------
:func:`write_value` stores one cell and does everything the write implies in
the same transaction:

1. Relation values are sanitised, and a timeline relation's ``_timeline`` is
   derived from its ``relationPool`` (see ``app.blocks.timeline``).
2. Mirrors are kept in sync: the other side of a bilateral relation, the
   ``sub_item`` list of the old and new parent of a ``parent_item``, the
   ``parent_item`` of the children of a ``sub_item``. Entry templates write
   no mirrors; they are no participant in any relation graph.
3. ``last_edited_*`` is refreshed.
4. What is computed from the changed values is brought up to date: the
   entry's own formulas and rollups, then those of other entries that read a
   changed property through a relation linking this entry, and the same for
   every entry that received a mirror value (see
   ``app.blocks.computed.recompute_after_value_change``).

:func:`copy_entry` (duplicate) and :func:`apply_template` reuse the mirror
steps for every value they copy.
"""
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.blocks import relation_lock
from app.blocks import repository as repo
from app.blocks import service
from app.blocks.computed import (
    READS_TITLE,
    SchemaIndex,
    compute_all_for_entry,
    compute_cross_db_dependents,
    compute_same_db_rollup_dependents,
    recompute_after_value_change,
)
from app.blocks.readonly_properties import (
    READONLY_TYPES,
    populate_readonly_properties,
    refresh_last_edited,
)
from app.blocks.relation_sync import (
    get_mirror_schema,
    sync_bilateral_relation,
    sync_bilateral_relation_timeline,
    sync_parent_item,
    sync_sub_item,
    would_create_parent_cycle,
)
from app.blocks.timeline import (
    extract_related_ids_now,
    pool_to_timeline,
    sanitize_flat_nuances,
    sanitize_nuance_pool,
    validate_timeline_value,
)

# Mirror values written for one change: the mirror property and the entries
# whose value of it was written. Those entries changed too, so what is
# computed from them is brought up to date along with the entry itself.
MirrorWrites = list[tuple[Any, set[uuid.UUID]]]

_BILATERAL_DIRECTIONS = ("bilateral", "bilateral_self")


# ─── Relation helpers ─────────────────────────────────────────────────────────


def is_bilateral(schema) -> bool:
    """Whether *schema* is a relation that keeps a mirror side in sync."""
    return (
        schema.type == "relation"
        and (schema.config or {}).get("direction") in _BILATERAL_DIRECTIONS
    )


def is_relation_timeline(schema) -> bool:
    """Whether *schema* is a relation with a timeline (pool format)."""
    return schema.type == "relation" and bool((schema.config or {}).get("hasTimeline", False))


def foreign_target(schema, database_id: uuid.UUID) -> Optional[str]:
    """The target database of a relation, as a string, unless it is *database_id*."""
    raw = (schema.config or {}).get("target_database_id")
    if raw and str(raw) != str(database_id):
        return str(raw)
    return None


def resolve_partner_schema(all_schemas, schema):
    """
    The partner of a ``parent_item`` or ``sub_item`` property among
    *all_schemas*, found through ``config.partner_schema_id``, or None.
    """
    partner_id_str = (schema.config or {}).get("partner_schema_id")
    if not partner_id_str:
        return None
    return next((s for s in all_schemas if str(s.id) == partner_id_str), None)


@dataclass
class MirrorSync:
    """What keeping the mirror of a bilateral relation in sync did."""

    mirror_schema: Any
    written: set[uuid.UUID]
    # The database whose mirror property the sync created, if it did.
    created_in: Optional[uuid.UUID] = None


def sync_relation_mirror(
    db: Session,
    schema,
    entry_id: uuid.UUID,
    new_value: Optional[dict],
    old_value: Optional[dict],
) -> MirrorSync:
    """
    Bring the mirror side of the bilateral relation *schema* in line with
    *entry_id*'s change from *old_value* to *new_value*.

    A timeline relation diffs the pools; any other relation diffs the current
    links, and may create a missing mirror property on the way, which is
    reported in ``created_in``.
    """
    if is_relation_timeline(schema):
        mirror_schema = get_mirror_schema(db, schema)
        written = sync_bilateral_relation_timeline(
            db, schema, mirror_schema, entry_id,
            dict((new_value or {}).get("relationPool") or {}),
            old_pool=dict((old_value or {}).get("relationPool") or {}),
            new_nuance_pool=dict((new_value or {}).get("nuancePool") or {}),
        )
        return MirrorSync(mirror_schema, written)

    mirror_missing = get_mirror_schema(db, schema) is None
    written = sync_bilateral_relation(
        db, schema, entry_id,
        list(extract_related_ids_now(new_value)),
        old_related_ids=extract_related_ids_now(old_value),
        new_nuances=dict((new_value or {}).get("nuances") or {}),
    )
    # Resolved after the sync, which may have created the mirror.
    mirror_schema = get_mirror_schema(db, schema)
    created_in = (
        mirror_schema.database_id
        if mirror_missing and mirror_schema is not None
        else None
    )
    return MirrorSync(mirror_schema, written, created_in)


def sync_hierarchy_mirror(
    db: Session,
    schema,
    entry_id: uuid.UUID,
    new_ids: list[str],
    old_ids: list[str],
    partner_schema,
) -> MirrorWrites:
    """
    Keep the partner of a ``parent_item`` or ``sub_item`` value in sync and
    return the mirror values written.

    For ``sub_item`` the previous parents of newly adopted children lose them
    from their own ``sub_item`` list, which is reported as a write of
    *schema* itself.
    """
    if schema.type == "parent_item":
        written = sync_parent_item(db, entry_id, new_ids, old_ids, partner_schema)
        return [(partner_schema, written)] if partner_schema is not None else []
    written, previous_parents = sync_sub_item(
        db, entry_id, new_ids, old_ids, partner_schema, schema.id,
    )
    if partner_schema is None:
        return []
    return [(partner_schema, written), (schema, previous_parents)]


def recompute_after_writes(
    db: Session,
    database_id: uuid.UUID,
    entry_id: uuid.UUID,
    changed: set,
    mirror_writes: MirrorWrites,
) -> list[uuid.UUID]:
    """
    Bring what is computed from a change up to date and return the databases
    whose entries were recomputed.

    *changed* names the properties of *entry_id* that changed (``READS_TITLE``
    for the title or the icon). The entries in *mirror_writes* are treated the
    same way for their mirror property, so their own rollups follow the link
    they gained or lost. The schemas of all databases are loaded once for all of
    these, after the mirror syncs, which may have created a mirror property.
    """
    index = SchemaIndex.load(db)
    affected = recompute_after_value_change(db, database_id, entry_id, changed, index=index)
    for mirror_schema, pages in mirror_writes:
        for page_id in pages:
            affected += recompute_after_value_change(
                db, mirror_schema.database_id, page_id, {mirror_schema.id}, index=index,
            )
    return affected


# ─── Writing a value ──────────────────────────────────────────────────────────


def check_writable(schema) -> None:
    """Refuse a write to a system-managed property with 422."""
    if schema.type in READONLY_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Property '{schema.name}' is system-managed and cannot be set directly.",
        )


def check_parent_item_value(
    db: Session,
    entry_id: uuid.UUID,
    schema,
    value: Optional[dict],
) -> None:
    """
    Refuse a ``parent_item`` value with 422 when it names more than one
    parent (single-parent policy), an invalid id, or a parent that would make
    the hierarchy circular.
    """
    if schema.type != "parent_item" or value is None:
        return
    related = value.get("related_ids") or []
    if len(related) > 1:
        raise HTTPException(
            status_code=422,
            detail="parent_item only supports a single parent (single-parent policy).",
        )
    if len(related) == 1:
        try:
            proposed_parent_id = uuid.UUID(related[0])
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid parent entry ID.")
        if would_create_parent_cycle(db, entry_id, proposed_parent_id, schema.id):
            raise HTTPException(
                status_code=422,
                detail="Setting this parent would create a cycle in the hierarchy.",
            )


def check_timeline_value(schema, value: Optional[dict]) -> None:
    """
    Refuse a malformed timeline value with 422.

    A timeline relation accepts only ``relationPool`` writes; its
    ``_timeline`` is derived by the backend. Any other timeline property is
    checked for a well-formed ``_timeline``.
    """
    if value is None:
        return
    if is_relation_timeline(schema):
        if "_timeline" in value and "relationPool" not in value:
            raise HTTPException(
                status_code=422,
                detail=(
                    "For timeline relations, write to 'relationPool' — "
                    "'_timeline' is computed automatically by the backend."
                ),
            )
    elif (schema.config or {}).get("hasTimeline", False) and "_timeline" in value:
        err = validate_timeline_value(value)
        if err:
            raise HTTPException(status_code=422, detail=f"Invalid timeline value: {err}")


def build_stored_value(schema, value: Optional[dict]) -> Optional[dict]:
    """
    The value to store for *value* sent by the client.

    A timeline relation keeps its pool and the nuance labels of ranges it
    has, and gains the derived ``_timeline``. Any other relation keeps its
    links and the nuance labels of linked entries. Other values are stored as
    sent.
    """
    if value is None or schema.type != "relation":
        return value
    if is_relation_timeline(schema):
        new_pool = dict(value.get("relationPool") or {})
        new_nuance_pool = sanitize_nuance_pool(value.get("nuancePool"), new_pool)
        stored: dict = {"relationPool": new_pool}
        if new_nuance_pool:
            stored["nuancePool"] = new_nuance_pool
        stored["_timeline"] = pool_to_timeline(new_pool, new_nuance_pool)
        return stored
    new_related_ids = list(value.get("related_ids") or [])
    flat_nuances = sanitize_flat_nuances(value.get("nuances"), new_related_ids)
    stored = {"related_ids": new_related_ids}
    if flat_nuances:
        stored["nuances"] = flat_nuances
    return stored


def _value_before(db: Session, entry_id: uuid.UUID, schema) -> Optional[dict]:
    """
    A copy of the value the mirrors are diffed against, read before the
    write; None for properties without mirrors.

    Copied, because the write changes the same row object.
    """
    if not (is_bilateral(schema) or schema.type in ("parent_item", "sub_item")):
        return None
    pv = repo.get_value(db, entry_id, schema.id)
    if pv is None or not pv.value:
        return None
    return dict(pv.value)


@dataclass
class ValueWrite:
    """The outcome of :func:`write_value`."""

    # Databases other than the entry's whose entries were recomputed.
    affected_db_ids: list[uuid.UUID]
    # The database whose mirror property the write created, if any.
    mirror_created_in: Optional[uuid.UUID] = None
    # The target database of a bilateral relation in another database.
    bilateral_target: Optional[str] = None


def write_value(
    db: Session,
    database_id: uuid.UUID,
    entry,
    schema,
    value: Optional[dict],
    user_id: uuid.UUID,
) -> ValueWrite:
    """
    Store *value* for *entry* and *schema* with everything the write implies
    (see the module docstring). The caller has checked access and the value
    and holds the relation locks.
    """
    entry_id = entry.id
    check_timeline_value(schema, value)

    old_value = _value_before(db, entry_id, schema)
    partner_schema = None
    if schema.type in ("parent_item", "sub_item") and (schema.config or {}).get("partner_schema_id"):
        partner_schema = resolve_partner_schema(repo.list_schemas(db, database_id), schema)

    stored_value = build_stored_value(schema, value)
    repo.upsert_value(db, page_id=entry_id, schema_id=schema.id, value=stored_value)

    result = ValueWrite(affected_db_ids=[])
    mirror_writes: MirrorWrites = []
    if is_bilateral(schema) and (schema.config or {}).get("direction") == "bilateral":
        result.bilateral_target = foreign_target(schema, database_id)

    if entry.type != "entry_template":
        if is_bilateral(schema):
            sync = sync_relation_mirror(db, schema, entry_id, stored_value, old_value)
            result.mirror_created_in = sync.created_in
            if sync.mirror_schema is not None:
                mirror_writes.append((sync.mirror_schema, sync.written))
        elif schema.type in ("parent_item", "sub_item"):
            mirror_writes += sync_hierarchy_mirror(
                db, schema, entry_id,
                list((stored_value or {}).get("related_ids") or []),
                list((old_value or {}).get("related_ids") or []),
                partner_schema,
            )

    edited = refresh_last_edited(db, database_id, entry_id, user_id)
    affected = recompute_after_writes(
        db, database_id, entry_id, {schema.id} | edited, mirror_writes,
    )
    result.affected_db_ids = list(dict.fromkeys(
        db_id for db_id in affected if db_id != database_id
    ))
    return result


# ─── Duplicating an entry ─────────────────────────────────────────────────────


@dataclass
class EntryCopy:
    """The outcome of :func:`copy_entry`."""

    block: Any
    # Other databases with changed entries: relation targets and cascades.
    other_db_ids: set[str] = field(default_factory=set)


def copy_entry(
    db: Session,
    database_id: uuid.UUID,
    original,
    original_pvs,
    schema_map: dict,
    user_id: uuid.UUID,
) -> EntryCopy:
    """
    Create a copy of *original* right after it.

    Copies the content, icon, state and every writable value from
    *original_pvs*, and mirrors bilateral relations as a fresh link from the
    copy. System-managed properties get the copy's own values, computed
    properties are evaluated, and rollups elsewhere that now reach the copy
    through a mirror are recomputed.
    """
    new_block = service.create_block(db, type="page", parent_id=database_id)
    new_block.content = dict(original.content) if original.content else None
    new_block.icon = original.icon
    new_block.state = original.state

    bilateral_target_dbs: set[str] = set()
    for pv in original_pvs:
        schema = schema_map.get(pv.property_schema_id)
        if schema is None or schema.type in READONLY_TYPES or pv.value is None:
            continue
        repo.upsert_value(
            db, page_id=new_block.id, schema_id=pv.property_schema_id, value=pv.value,
        )
        if is_bilateral(schema):
            # The copy is a fresh entry without links on the mirror side yet.
            sync_relation_mirror(db, schema, new_block.id, pv.value, None)
            target = foreign_target(schema, database_id)
            if target:
                bilateral_target_dbs.add(target)

    populate_readonly_properties(db, database_id, new_block.id, new_block.created_at, user_id)
    compute_all_for_entry(db, database_id, new_block.id)
    # Mirror entries that gained a link to the copy have rollups that must
    # now include it, in this database and in others.
    compute_same_db_rollup_dependents(db, database_id, new_block.id)
    cascade_db_ids = compute_cross_db_dependents(db, database_id, new_block.id)

    return EntryCopy(
        block=new_block,
        other_db_ids=bilateral_target_dbs | {str(d) for d in cascade_db_ids},
    )


# ─── Applying a template ──────────────────────────────────────────────────────


@dataclass
class TemplateApplication:
    """The outcome of :func:`apply_template`."""

    # Other databases with changed entries: relation targets and cascades.
    other_db_ids: set[str] = field(default_factory=set)
    # Databases whose mirror property the application created.
    mirrors_created_in: set[uuid.UUID] = field(default_factory=set)


def apply_template(
    db: Session,
    database_id: uuid.UUID,
    template,
    target,
    template_pvs,
    all_schemas,
    user_id: uuid.UUID,
) -> TemplateApplication:
    """
    Copy the content, icon and writable values of *template* onto *target*.

    Mirrors are diffed against the value the entry held before, so entries it
    no longer links lose their back-reference, as when the value is edited by
    hand. Computed values are then brought up to date as after a value
    write, for everything the template changed, the title and
    ``last_edited_*`` included.
    """
    entry_id = target.id
    schema_map = {s.id: s for s in all_schemas}
    result = TemplateApplication()
    bilateral_target_dbs: set[str] = set()
    changed_schema_ids: set = set()
    mirror_writes: MirrorWrites = []

    if template.content:
        target.content = dict(template.content)
        changed_schema_ids.add(READS_TITLE)
    if template.icon and template.icon != target.icon:
        target.icon = template.icon
        # Relation chips elsewhere show the icon as well as the title.
        changed_schema_ids.add(READS_TITLE)
    db.flush()

    for pv in template_pvs:
        schema = schema_map.get(pv.property_schema_id)
        if schema is None or schema.type in READONLY_TYPES or pv.value is None:
            continue

        # The value the entry holds before the template overwrites it; the
        # mirrors are diffed against it. Copied, because the upsert below
        # changes the same row object.
        old_value: Optional[dict] = None
        if schema.type in relation_lock.RELATION_FAMILY_TYPES:
            old_pv = repo.get_value(db, entry_id, pv.property_schema_id)
            if old_pv is not None and old_pv.value:
                old_value = dict(old_pv.value)

        repo.upsert_value(db, page_id=entry_id, schema_id=pv.property_schema_id, value=pv.value)
        changed_schema_ids.add(pv.property_schema_id)

        if is_bilateral(schema):
            sync = sync_relation_mirror(db, schema, entry_id, pv.value, old_value)
            if sync.created_in is not None:
                result.mirrors_created_in.add(sync.created_in)
            if sync.mirror_schema is not None:
                mirror_writes.append((sync.mirror_schema, sync.written))
            target_db = foreign_target(schema, database_id)
            if target_db:
                bilateral_target_dbs.add(target_db)

        if schema.type in ("parent_item", "sub_item"):
            mirror_writes += sync_hierarchy_mirror(
                db, schema, entry_id,
                list(extract_related_ids_now(pv.value)),
                list((old_value or {}).get("related_ids") or []),
                resolve_partner_schema(all_schemas, schema),
            )

    changed_schema_ids |= refresh_last_edited(db, database_id, entry_id, user_id)
    affected = recompute_after_writes(db, database_id, entry_id, changed_schema_ids, mirror_writes)
    result.other_db_ids = bilateral_target_dbs | {str(d) for d in affected if d != database_id}
    return result
