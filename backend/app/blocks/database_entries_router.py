"""
Database entries router.

HTTP interface for the entries of database blocks: entry listing and the
server-side query (with embedded property values), title resolution for
relation chips, single and batch entry loads, entry creation and
duplication, per-cell value upsert, and entry template management. The
databases and their property schemas are served by
``app.blocks.database_router`` under the same prefix.

All endpoints require a valid session cookie. The steps of writing values,
duplicating entries and applying templates live in
``app.blocks.entry_service``; this module checks access, takes the relation
locks, runs them in one transaction, commits and broadcasts.

Access
------
Every endpoint resolves its database through
``app.blocks.database_access.get_database_or_raise``. Reads answer 404 when
the caller may not access the database and changes 403. The entry listings
that relation pickers call (``GET /entries``, ``POST /entries/query``,
``POST /entries/resolve-titles``) answer with an empty result instead.

Writing a relation value also requires access to the relation's target
database, because a bilateral relation writes back-references there.

Convention
----------
* Every handler is a synchronous ``def``. FastAPI runs those in a worker
  thread, so their blocking database work — including the formula and rollup
  cascades of a value change — never stalls the event loop that serves all
  other requests and the open WebSocket connections.
* Mutation handlers broadcast through ``app.blocks.database_events`` after a
  successful ``db.commit()``, passing their session so the recipient check
  costs no extra connection.
* Every handler that writes relation values, and with them a mirror, first
  takes the relation locks of the databases involved
  (``app.blocks.relation_lock``), before it reads the old value its diff is
  computed against. Concurrent writes that reach the same mirror therefore
  run one after another instead of overwriting each other's change.

Computed properties (formula / rollup)
---------------------------------------
Formula and rollup values are always produced by the backend. Direct writes
via the upsert endpoint are rejected with 422. Values are (re-)computed
automatically on entry creation, after readonly properties are populated,
and on every value upsert, for what the change reaches.

Entry templates
---------------
Templates are real ``entry_template``-type blocks stored as direct children
of a database block, identical in structure to regular ``page``-type entries.
They participate in the schema lifecycle automatically: adding or removing a
property schema affects templates and real entries alike because they share
the same parent-child relationship and PropertyValue table.

Templates are excluded from all regular entry queries
(``GET /entries``, ``POST /entries/query``) and from formula / rollup
aggregation by filtering ``type != 'entry_template'`` at the repository
layer. Their relation values write no mirrors.

POST   /{database_id}/entry-templates               – create a template
GET    /{database_id}/entry-templates               – list all templates
POST   /{database_id}/entry-templates/{tid}/apply/{entry_id}
                                                    – copy template content
                                                      and writable values
                                                      onto an existing entry
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.automations.automations_engine import TriggerEvent
from app.automations.automations_engine import receive as automation_receive
from app.blocks import entry_service
from app.blocks import relation_lock
from app.blocks import repository as repo
from app.blocks import resolution, service, views
from app.blocks.computed import compute_all_for_entry
from app.blocks.database_access import (
    Access,
    copied_relation_configs,
    default_entry_icon,
    get_database_or_raise,
    require_relation_targets_access,
)
from app.blocks.database_events import (
    emit_entries_updated,
    emit_entries_updated_for,
    emit_entry_created,
    emit_schema_updated,
)
from app.blocks.database_payloads import (
    EntryBatchRequest,
    EntryBatchResponse,
    EntryDetailResponse,
    EntryQueryRequest,
    EntryQueryResponse,
    EntryResponse,
    EntryTemplateResponse,
    EntryTitleRequest,
    EntryTitleResponse,
    QueryFilter,
    QueryFilterGroup,
    ValueResponse,
    ValueUpsert,
)
from app.blocks.readonly_properties import populate_readonly_properties
from app.blocks.router import get_db
from app.blocks.service import BlockConflict, BlockNotFound
from app.session.deps import get_current_user
from app.users.model import User

logger = logging.getLogger(__name__)

database_entries_router = APIRouter(prefix="/api/databases", tags=["databases"])


# ─── Entry endpoints ──────────────────────────────────────────────────────────


@database_entries_router.get(
    "/{database_id}/entries",
    response_model=list[EntryResponse],
)
def list_entries(
    database_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return all active entry blocks of a database, enriched with their
    property values.

    Values are loaded in a single additional query (no N+1). The ``values``
    dict on each entry maps schema_id (string) to the stored JSONB payload.
    Non-admin users that do not have access to the database block receive an
    empty list (this prevents leaking entry content via relation pickers).
    """
    if get_database_or_raise(db, database_id, current_user, Access.SOFT) is None:
        return []
    entries = repo.list_children(
        db, database_id, state="active",
        exclude_types=frozenset({"entry_template"}),
    )
    if not entries:
        return []

    values_map = repo.list_values_for_pages(db, [e.id for e in entries])
    return [
        EntryResponse(
            id=entry.id,
            position=entry.position,
            content=entry.content,
            icon=entry.icon,
            state=entry.state,
            values={
                str(pv.property_schema_id): pv.value
                for pv in values_map.get(entry.id, [])
            },
        )
        for entry in entries
    ]


@database_entries_router.post(
    "/{database_id}/entries/query",
    response_model=EntryQueryResponse,
)
def query_entries(
    database_id: uuid.UUID,
    payload: EntryQueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return a filtered, sorted, paginated list of database entries.

    The frontend sends its active view's filter and sort descriptors as the
    request body; all filtering and sorting is executed in the database rather
    than in the browser.

    Filters
    -------
    All filter conditions are ANDed together.  Each condition targets either
    the special ``'__name__'`` schema (the entry title stored in
    ``Block.content['title']``) or a real PropertySchema by UUID.  The
    operators mirror those supported by the client-side filter panel.

    Sorts
    -----
    Sort columns are applied in order; ``Block.position`` is appended as an
    implicit final tiebreaker so the result is always deterministic.

    Pagination
    ----------
    ``limit`` is capped at 10 000 server-side.  ``total`` in the response
    reflects the full filtered count before the limit is applied.

    Permission
    ----------
    Non-admin users that do not have access to the database block receive an
    empty result set. This cleanly prevents relation-cell pickers from
    surfacing entries from databases the user may not see.
    """
    if get_database_or_raise(db, database_id, current_user, Access.SOFT) is None:
        return EntryQueryResponse(entries=[], total=0)
    schemas = repo.list_schemas(db, database_id)
    schema_map: dict[str, object] = {str(s.id): s for s in schemas}

    def _resolve_filter(f: QueryFilter) -> repo.FilterDescriptor | None:
        """Resolve one QueryFilter into a FilterDescriptor, or None to skip it."""
        return repo.resolve_filter_descriptor(
            schema_map,
            schema_id=f.schema_id,
            operator=f.operator,
            value=f.value,
            date_mode=f.date_mode,
            date_offset=f.date_offset,
            formula_result_type=f.formula_result_type,
            value2=f.value2,
        )

    # Prefer filter_groups; fall back to legacy flat filters field (single AND group)
    raw_groups = payload.filter_groups
    if not raw_groups and payload.filters:
        raw_groups = [QueryFilterGroup(conjunction='and', filters=payload.filters)]

    resolved_groups: list[repo.FilterGroupDescriptor] = []
    for group in raw_groups:
        resolved_filters = [d for f in group.filters if (d := _resolve_filter(f)) is not None]
        if resolved_filters:
            resolved_groups.append(repo.FilterGroupDescriptor(
                conjunction=group.conjunction,
                filters=resolved_filters,
            ))

    # Resolve sort descriptors
    resolved_sorts: list[repo.SortDescriptor] = []
    for s in payload.sorts:
        if s.schema_id == '__name__':
            schema_type = None
            schema_config = None
        else:
            schema = schema_map.get(s.schema_id)
            if schema is None:
                continue
            schema_type = schema.type
            schema_config = schema.config
        resolved_sorts.append(
            repo.SortDescriptor(
                schema_id=s.schema_id,
                schema_type=schema_type,
                schema_config=schema_config,
                direction=s.direction,
            )
        )

    limit = min(max(1, payload.limit), 10_000)
    offset = max(0, payload.offset)

    entries, total = repo.query_entries(
        db, database_id, resolved_groups, resolved_sorts, limit, offset
    )

    if not entries:
        return EntryQueryResponse(entries=[], total=total)

    values_map = repo.list_values_for_pages(db, [e.id for e in entries])
    return EntryQueryResponse(
        entries=[
            EntryResponse(
                id=entry.id,
                position=entry.position,
                content=entry.content,
                icon=entry.icon,
                state=entry.state,
                values={
                    str(pv.property_schema_id): pv.value
                    for pv in values_map.get(entry.id, [])
                },
            )
            for entry in entries
        ],
        total=total,
    )


@database_entries_router.post(
    "/{database_id}/entries/resolve-titles",
    response_model=list[EntryTitleResponse],
)
def resolve_entry_titles(
    database_id: uuid.UUID,
    payload: EntryTitleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Resolve a set of entry IDs to lightweight ``{id, title, database_id,
    icon}`` descriptors for rendering relation chips.

    Relation cells store only the target entry IDs.  Resolving their titles
    from the paginated entry listing breaks down once a relation points past
    the active display limit, because the limited page never contains the
    linked entry.  This endpoint loads exactly the requested IDs in a single
    query, independent of any limit, so a chip renders regardless of the target
    database's pagination position of its entry.

    Only active, non-template entries that are direct children of
    ``database_id`` are returned; unknown, trashed or foreign IDs are silently
    omitted, which doubles as the soft-deleted-entry filter the relation cell
    relied on before.

    Key values
    ----------
    When ``value_schema_id`` is supplied, each descriptor also carries the raw
    value stored for that property.  Keyed relations need this to sort and
    render their linked entries, and they need it on exactly this path: reading
    key values off the paginated entry cache would reintroduce the display-limit
    blind spot this endpoint exists to close, silently and only for large target
    databases.

    A ``value_schema_id`` that does not resolve to a property of this database
    yields ``None`` values rather than an error, so a key pointer left dangling
    by a concurrent delete degrades the cell to seniority order instead of
    breaking it.

    The resolution itself is ``app.blocks.resolution.resolve_titles``, the
    same implementation view pages and single entries use for their relations.

    Permission
    ----------
    Non-admin users that do not have access to the database block receive an
    empty list, mirroring the listing endpoints so relation chips never leak
    titles from databases the user may not see.
    """
    if get_database_or_raise(db, database_id, current_user, Access.SOFT) is None:
        return []
    if not payload.ids:
        return []
    return [
        EntryTitleResponse(
            id=title.id,
            title=title.title,
            database_id=title.database_id,
            value=title.value,
            icon=title.icon,
        )
        for title in resolution.resolve_titles(
            db, database_id, payload.ids, payload.value_schema_id
        )
    ]


@database_entries_router.get(
    "/{database_id}/entries/{entry_id}",
    response_model=EntryDetailResponse,
)
def get_entry(
    database_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return one entry with its property values and resolved relations.

    For the entry page and the side view, which used to load the whole
    database to find a single entry in it. Entry templates are entries too
    and are returned the same way.

    Answers 404 when the entry is not a child of the database, and when the
    caller may not read the database, so the response does not confirm that
    either exists.
    """
    if get_database_or_raise(db, database_id, current_user, Access.SOFT) is None:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    entry = repo.get_block(db, entry_id)
    if entry is None or entry.parent_id != database_id:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")

    payload = views.entry_payloads(db, [entry])[0]
    resolved = resolution.resolve_relations(
        db, repo.list_schemas(db, database_id), [payload["values"]], current_user,
    )
    return EntryDetailResponse(entry=payload, **resolved.as_payload())


@database_entries_router.post(
    "/{database_id}/entries/batch",
    response_model=EntryBatchResponse,
)
def get_entries_batch(
    database_id: uuid.UUID,
    payload: EntryBatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the given entries with their values and resolved relations.

    For change events: a client that learns which entries changed loads just
    those, in one request, instead of everything it shows. Only active entries
    of the database are returned; the other ids come back in ``missing``.

    Answers 404 when the caller may not read the database.
    """
    get_database_or_raise(db, database_id, current_user, Access.READ)

    wanted = list(dict.fromkeys(payload.ids))
    found = repo.list_blocks_by_ids(
        db, wanted, parent_id=database_id, state="active",
        exclude_types=frozenset({"entry_template"}),
    )
    by_id = {b.id: b for b in found}
    entries = [by_id[i] for i in wanted if i in by_id]
    payloads = views.entry_payloads(db, entries)
    resolved = resolution.resolve_relations(
        db, repo.list_schemas(db, database_id), (p["values"] for p in payloads), current_user,
    )
    return EntryBatchResponse(
        entries=payloads,
        missing=[i for i in wanted if i not in by_id],
        **resolved.as_payload(),
    )


@database_entries_router.post(
    "/{database_id}/entries",
    response_model=EntryResponse,
    status_code=201,
)
def create_entry(
    database_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new page-type entry block inside the database.

    The entry is appended after all existing siblings and starts with the
    database's default entry icon, when one is set. Returns the new entry
    with an empty values map.

    After creating the entry, readonly properties are populated and all
    formula / rollup schemas are evaluated (initial values are null /
    empty-context results).
    """
    get_database_or_raise(db, database_id, current_user, Access.WRITE)
    try:
        block = service.create_block(
            db, type="page", parent_id=database_id,
            icon=default_entry_icon(db, database_id),
        )
        populate_readonly_properties(db, database_id, block.id, block.created_at, current_user.id)
        compute_all_for_entry(db, database_id, block.id)
        db.commit()
        emit_entry_created(db, database_id, block)
        return EntryResponse(
            id=block.id,
            position=block.position,
            content=block.content,
            icon=block.icon,
            state=block.state,
            values={},
        )
    except BlockNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except BlockConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        db.rollback()
        raise


@database_entries_router.post(
    "/{database_id}/entries/{entry_id}/duplicate",
    response_model=EntryResponse,
    status_code=201,
)
def duplicate_entry(
    database_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Duplicate an existing database entry.

    Creates a new page-type block and copies over:

    * ``content``  – full content dict (title, cover, etc.)
    * ``icon``     – entry icon
    * ``state``    – block state (e.g. 'active')
    * All writable property values – readonly types (id, created_*, formula,
      rollup) are intentionally excluded so the new entry gets its own
      auto-generated values.

    Bilateral relation properties are mirrored with the same helpers the
    regular upsert endpoint uses (``entry_service.copy_entry``).

    The entire operation runs inside a single transaction.  One
    ``database_entries_updated`` broadcast is sent for the source database;
    additional broadcasts are sent for any bilateral relation target databases
    whose mirror values were written and for databases whose rollups were
    recomputed.

    Copying a relation value requires access to the relation's target
    database and answers 403 otherwise, as writing that value would.

    Raises 404 if either the database or the original entry is not found.
    """
    get_database_or_raise(db, database_id, current_user, Access.WRITE)
    original = repo.get_block(db, entry_id)
    if original is None or original.parent_id != database_id:
        raise HTTPException(
            status_code=404,
            detail=f"Entry {entry_id} not found in database {database_id}",
        )

    # The copy links the targets of the original's relations, which rewrites
    # their mirrors; wait for other writes to them first.
    relation_lock.lock_relation_databases(
        db, relation_lock.databases_for_database(db, database_id),
    )

    # Snapshot all property values before the write, so SQLAlchemy's identity
    # map cannot mix up old and new rows.
    original_pvs = repo.list_values_for_pages(db, [entry_id]).get(entry_id, [])
    schema_map = {s.id: s for s in repo.list_schemas(db, database_id)}
    require_relation_targets_access(
        db, database_id, copied_relation_configs(original_pvs, schema_map), current_user,
    )

    try:
        copy = entry_service.copy_entry(
            db, database_id, original, original_pvs, schema_map, current_user.id,
        )
        new_block = copy.block
        db.commit()
        db.refresh(new_block)
        new_values: dict[str, Optional[dict]] = {
            str(pv.property_schema_id): pv.value
            for pv in repo.list_values_for_pages(db, [new_block.id]).get(new_block.id, [])
        }
    except BlockNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except BlockConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        db.rollback()
        raise

    emit_entries_updated(db, database_id)
    emit_entries_updated_for(db, copy.other_db_ids)

    return EntryResponse(
        id=new_block.id,
        position=new_block.position,
        content=new_block.content,
        icon=new_block.icon,
        state=new_block.state,
        values=new_values,
    )


@database_entries_router.put(
    "/{database_id}/entries/{entry_id}/values/{schema_id}",
    response_model=ValueResponse,
)
def upsert_value(
    database_id: uuid.UUID,
    entry_id: uuid.UUID,
    schema_id: uuid.UUID,
    payload: ValueUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create or update the property value for a specific entry / schema pair.

    The ``value`` field may be ``null`` to explicitly clear the cell.

    Returns 200 with the value as stored (see ``ValueResponse``), read after
    the commit in the same shape the read endpoints use. The backend derives
    parts of some values and sanitises others, for instance the ``_timeline``
    of a timeline relation computed from its ``relationPool``, so the client
    replaces the value it sent with this one.

    The write itself, the mirror syncs and the recompute of what is computed
    from the change are ``entry_service.write_value``. After the commit:

    1. When the write created a missing mirror property,
       ``database_schema_updated`` is broadcast to its database first, so open
       views know the property when the entries event names it.
    2. ``database_entries_updated`` is broadcast for this database, for every
       database whose entries were recomputed, and for the target database of
       a bilateral relation.
    3. The automation engine is notified; its failures are logged and never
       reach the caller.

    Raises 404 if the entry does not belong to this database, or if the
    schema does not belong to this database.
    Raises 422 for schema types that are backend-managed (id, created_*, formula,
    rollup), for an invalid ``parent_item`` or timeline value.
    Raises 403 for a relation property whose target database the caller may
    not access.
    """
    get_database_or_raise(db, database_id, current_user, Access.WRITE)
    entry = repo.get_block(db, entry_id)
    if entry is None or entry.parent_id != database_id:
        raise HTTPException(
            status_code=404,
            detail=f"Entry {entry_id} not found in database {database_id}",
        )
    schema = repo.get_schema(db, schema_id)
    if schema is None or schema.database_id != database_id:
        raise HTTPException(
            status_code=404,
            detail=f"Schema {schema_id} not found in database {database_id}",
        )
    entry_service.check_writable(schema)
    if schema.type == "relation":
        require_relation_targets_access(db, database_id, [schema.config], current_user)

    # Relation values are diffed against their stored state and rewrite their
    # mirrors, so the write waits for any other one reaching the same mirrors
    # before it reads anything (see app.blocks.relation_lock).
    if schema.type in relation_lock.RELATION_FAMILY_TYPES:
        relation_lock.lock_relation_databases(
            db, relation_lock.databases_for_schema(database_id, schema),
        )

    entry_service.check_parent_item_value(db, entry_id, schema, payload.value)

    try:
        write = entry_service.write_value(
            db, database_id, entry, schema, payload.value, current_user.id,
        )
        db.commit()
        # The committed value, serialised by the read path itself so the
        # answer cannot drift from what a later read returns.
        stored = views.entry_payloads(db, [entry])[0]["values"].get(str(schema_id))
    except Exception:
        db.rollback()
        raise

    if write.mirror_created_in is not None:
        emit_schema_updated(db, write.mirror_created_in)
    emit_entries_updated(db, database_id)
    emit_entries_updated_for(db, write.affected_db_ids)
    if write.bilateral_target:
        emit_entries_updated(db, write.bilateral_target)

    # Fires after all broadcasts so that any secondary updates triggered by
    # automations arrive after the primary one. A broken automation must not
    # surface as a request error.
    try:
        automation_receive(
            TriggerEvent(
                action_type="PropertyUpdate",
                origin="user",
                actor_uuid=str(current_user.id),
                db_uuid=str(database_id),
                property_uuid=str(schema_id),
                old_value="",   # TODO: serialise old cell value when needed
                new_value="",   # TODO: serialise new cell value when needed
                entry_id=str(entry_id),
            ),
            db,
        )
    except Exception as exc:
        logger.warning(
            "Automation engine error after upsert of schema %s: %s",
            schema_id,
            exc,
            exc_info=True,
        )

    return ValueResponse(value=stored)


# ─── Entry-template endpoints ─────────────────────────────────────────────────


@database_entries_router.post(
    "/{database_id}/entry-templates",
    response_model=EntryTemplateResponse,
    status_code=201,
)
def create_entry_template(
    database_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new entry template block inside *database_id*.

    The template is stored as a child block of the database with
    ``type = 'entry_template'``.  Readonly properties (id, created_*,
    last_edited_*, formula, rollup) are seeded immediately so the template
    displays the same property set as regular entries.

    Templates do not appear in any regular entry query or relation / rollup
    aggregation.
    """
    get_database_or_raise(db, database_id, current_user, Access.WRITE)
    try:
        block = service.create_block(db, type="entry_template", parent_id=database_id)
        populate_readonly_properties(db, database_id, block.id, block.created_at, current_user.id)
        compute_all_for_entry(db, database_id, block.id)
        db.commit()
        emit_entries_updated(db, database_id)
        return EntryTemplateResponse(
            id=block.id,
            position=block.position,
            content=block.content,
            icon=block.icon,
            values={},
        )
    except BlockNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except BlockConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        db.rollback()
        raise


@database_entries_router.get(
    "/{database_id}/entry-templates",
    response_model=list[EntryTemplateResponse],
)
def list_entry_templates(
    database_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return all active entry templates belonging to *database_id*.

    Values are loaded in a single additional query (no N+1), identical to
    the regular ``GET /entries`` endpoint.
    """
    get_database_or_raise(db, database_id, current_user, Access.READ)
    templates = [
        b for b in repo.list_children(db, database_id, state="active")
        if b.type == "entry_template"
    ]
    if not templates:
        return []
    values_map = repo.list_values_for_pages(db, [t.id for t in templates])
    return [
        EntryTemplateResponse(
            id=t.id,
            position=t.position,
            content=t.content,
            icon=t.icon,
            values={
                str(pv.property_schema_id): pv.value
                for pv in values_map.get(t.id, [])
            },
        )
        for t in templates
    ]


@database_entries_router.post(
    "/{database_id}/entry-templates/{template_id}/apply/{entry_id}",
    status_code=204,
)
def apply_entry_template(
    database_id: uuid.UUID,
    template_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Apply an entry template onto an existing database entry.

    Copies the template's ``content`` (title, icon) and all writable
    property values onto *entry_id*.  Readonly property types
    (``id``, ``created_*``, ``last_edited_*``, ``formula``, ``rollup``)
    are intentionally skipped — the target entry keeps its own
    auto-generated values for these.

    Bilateral relation side-effects, parent_item sync, and sub_item sync
    are applied using the same helpers as the regular upsert endpoint, diffed
    against the value the entry held before: entries it no longer links lose
    their back-reference, exactly as when the value is edited by hand.
    Computed values are then brought up to date the same way as after
    ``upsert_value``, for everything the template changed, the title and the
    ``last_edited_*`` properties included, and for the entries whose mirror
    value changed (see ``entry_service.apply_template``).

    Mirror properties the application created are announced first, then the
    entries of this database and of every other database it changed.

    Returns 204 No Content on success.
    Returns 404 if the database, template, or target entry is not found,
    or if the template / entry do not belong to the given database.
    Returns 403 if the template carries a relation value whose target
    database the caller may not access.
    """
    get_database_or_raise(db, database_id, current_user, Access.WRITE)

    template = repo.get_block(db, template_id)
    if template is None or template.parent_id != database_id or template.type != "entry_template":
        raise HTTPException(
            status_code=404,
            detail=f"Entry template {template_id} not found in database {database_id}",
        )

    target = repo.get_block(db, entry_id)
    if target is None or target.parent_id != database_id:
        raise HTTPException(
            status_code=404,
            detail=f"Entry {entry_id} not found in database {database_id}",
        )

    # Applying replaces relation values of the entry and rewrites the mirrors
    # of the entries linked before and after; wait for other writes to them
    # before the old values are read.
    relation_lock.lock_relation_databases(
        db, relation_lock.databases_for_database(db, database_id),
    )

    template_pvs = repo.list_values_for_pages(db, [template_id]).get(template_id, [])
    all_schemas = repo.list_schemas(db, database_id)
    require_relation_targets_access(
        db, database_id,
        copied_relation_configs(template_pvs, {s.id: s for s in all_schemas}),
        current_user,
    )

    try:
        application = entry_service.apply_template(
            db, database_id, template, target, template_pvs, all_schemas, current_user.id,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    for created_in in application.mirrors_created_in:
        emit_schema_updated(db, created_in)
    emit_entries_updated(db, database_id)
    emit_entries_updated_for(db, application.other_db_ids)
