"""
Database router.

HTTP interface for database block operations: property schema CRUD, entry
listing (with embedded property values), entry creation, per-cell value
upsert, and entry template management.

All endpoints require a valid session cookie. The service layer is used
only for entry creation (``service.create_block``); schema and value
operations go directly to the repository because they involve no cross-
cutting business logic.

Convention
----------
* Read-only handlers (GET) are synchronous ``def``.
* Mutation handlers that broadcast a WebSocket event are ``async def``.
* Schema CRUD and value upsert are ``def`` – they are contained mutations
  that do not require a broadcast (clients re-fetch after their own writes).

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
layer.

New endpoints
~~~~~~~~~~~~~
POST   /{database_id}/entry-templates               – create a template
GET    /{database_id}/entry-templates               – list all templates
POST   /{database_id}/entry-templates/{tid}/apply/{entry_id}
                                                    – copy template content
                                                      and writable values
                                                      onto an existing entry

Relation synchronisation
------------------------
When a ``relation``-type schema has ``config.direction == "bilateral"``:

* **At schema creation** the mirror schema is created immediately in the
  target database so it is visible before any entry is linked.
* **At value upsert** the mirror side is kept in sync within the same
  transaction (see ``_sync_bilateral_relation``).
* **At schema update**: if ``mirror_property_name`` changes the existing
  mirror schema is renamed (data preserved); if the source schema's own
  name changes the mirror's back-pointer config is updated accordingly.

When ``config.direction == "bilateral_self"``:

* No separate mirror schema is created — the schema mirrors itself.
* When entry A links to entry B via this property, B automatically links
  back to A in the same property (symmetric relation).
* All sync is handled by the same ``_sync_bilateral_relation`` helper.

Sub-item hierarchy (parent_item / sub_item)
-------------------------------------------
Every database automatically receives a linked ``parent_item`` /
``sub_item`` schema pair via ``seed_readonly_schemas``.

* **parent_item** – user-writable; stores at most one related entry ID
  (single-parent policy).  Writing this property triggers
  ``_sync_parent_item``, which maintains the mirror ``sub_item`` values
  bilaterally: the old parent's sub_item list loses the entry, the new
  parent's sub_item list gains it.  A cycle check prevents circular hierarchies.
* **sub_item** – also user-writable; writing it directly triggers
  ``_sync_sub_item``, which keeps the ``parent_item`` mirror in sync on
  each affected child entry.

Computed properties (formula / rollup)
---------------------------------------
Formula and rollup values are always produced by the backend.  Direct writes
via the upsert endpoint are rejected with 422.  Values are (re-)computed
automatically:

  * On entry creation, after readonly properties are populated.
  * On every value upsert (triggering downstream formula re-evaluation).
  * On schema config save (re-evaluating all entries).

Cycle detection runs on every formula / rollup schema create or update,
using Kahn's topological-sort algorithm.  A detected cycle returns 422.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.automations.automations_engine import TriggerEvent
from app.automations.automations_engine import receive as automation_receive
from app.blocks import repository as repo
from app.blocks import service
from app.blocks.computed import (
    _ROLLUP_FUNCTIONS,
    CycleError,
    SchemaLike,
    build_dependency_graph,
    compute_all_for_entry,
    compute_cross_db_dependents,
    compute_same_db_rollup_dependents,
    has_any_cycle,
)
from app.blocks.formula_engine import FormulaError, rename_prop_in_expression, validate_syntax
from app.blocks.router import get_db
from app.blocks.service import BlockConflict, BlockNotFound
from app.permissions import repository as perm_repo
from app.session.deps import get_current_user, require_session
from app.users.model import User
from app.ws.broadcaster import broadcast_block_event

logger = logging.getLogger(__name__)

database_router = APIRouter(prefix="/api/databases", tags=["databases"])

# Property types whose values are managed by the backend, not via the
# regular upsert endpoint.  An attempt to upsert these via the API is
# rejected with 422.  They are populated automatically on entry creation
# and (for last_edited_*, formula, rollup) on every value upsert.
# sub_item is user-writable (direct writes are accepted and trigger bilateral
# sync via _sync_sub_item); it is also maintained automatically as a mirror
# whenever parent_item is written.
_READONLY_TYPES: frozenset[str] = frozenset(
    {
        "id",
        "created_by",
        "created_time",
        "last_edited_by",
        "last_edited_time",
        "formula",
        "rollup",
    }
)


# ─── Request / Response schemas ───────────────────────────────────────────────


class SchemaCreate(BaseModel):
    name: str
    type: str
    position: Optional[float] = None
    config: Optional[dict] = None
    group: str = "Standard"


class SchemaUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    config: Optional[dict] = None
    position: Optional[float] = None
    group: Optional[str] = None


class SchemaResponse(BaseModel):
    id: uuid.UUID
    database_id: uuid.UUID
    name: str
    type: str
    config: Optional[dict]
    position: float
    group: str = "Standard"

    model_config = {"from_attributes": True}


class DatabaseListResponse(BaseModel):
    """Lightweight descriptor of a database block for relation pickers."""

    id: uuid.UUID
    title: Optional[str]

    model_config = {"from_attributes": True}


class EntryResponse(BaseModel):
    """
    A database entry (page-type block) enriched with all its property values.

    ``values`` maps schema_id (as string) to the JSONB value dict.  A missing
    key means the entry has never had a value written for that schema; ``null``
    means the value was explicitly cleared.
    """

    id: uuid.UUID
    position: float
    content: Optional[dict]
    icon: Optional[str]
    state: str
    values: dict[str, Optional[dict]]


class ValueUpsert(BaseModel):
    value: Optional[dict] = None


class FormulaValidateRequest(BaseModel):
    expression: str


class FormulaValidateResponse(BaseModel):
    valid: bool
    error: Optional[str] = None
    prop_names: list[str] = []


class QueryFilter(BaseModel):
    """A single filter condition as sent by the frontend."""
    schema_id: str
    operator: str
    value: str = ''
    date_mode: Optional[str] = None
    date_offset: Optional[int] = None
    formula_result_type: Optional[str] = None
    value2: str = ''


class QuerySort(BaseModel):
    """A single sort column as sent by the frontend."""
    schema_id: str
    direction: str = 'asc'


class QueryFilterGroup(BaseModel):
    """A group of filter conditions with a shared conjunction."""
    conjunction: str = 'and'  # 'and' | 'or'
    filters: list[QueryFilter] = []


class EntryQueryRequest(BaseModel):
    """
    POST body for the /entries/query endpoint.

    filter_groups – ordered list of filter groups.  Within each group,
                    conditions are combined by the group's conjunction
                    ('and'|'or').  Groups themselves are ANDed together.
    filters       – legacy flat filter list (treated as a single AND group).
                    Ignored when filter_groups is non-empty.
    sorts         – ordered sort columns; position is the implicit tiebreaker.
    limit         – maximum rows to return; capped server-side at 10 000.
    offset        – zero-based row offset for pagination.
    """
    filter_groups: list[QueryFilterGroup] = []
    filters: list[QueryFilter] = []  # backward compat
    sorts: list[QuerySort] = []
    limit: int = 1000
    offset: int = 0


class EntryQueryResponse(BaseModel):
    """Paginated filtered/sorted entry list."""
    entries: list[EntryResponse]
    total: int


class EntryTitleRequest(BaseModel):
    """
    POST body for the /entries/resolve-titles endpoint.

    ids – entry IDs to resolve to lightweight descriptors.  IDs that do not
          correspond to an active, non-template entry of the target database
          are silently omitted from the response.
    """
    ids: list[uuid.UUID] = []


class EntryTitleResponse(BaseModel):
    """
    Lightweight relation-target descriptor used to render relation chips.

    Carries only what a chip needs (``id``, ``title``, ``database_id``); the
    title is read from ``Block.content['title']`` and may be ``None`` for an
    untitled entry.
    """
    id: uuid.UUID
    title: Optional[str]
    database_id: uuid.UUID


# ─── Readonly-property helpers ────────────────────────────────────────────────


def _populate_readonly_properties(
    db: Session,
    database_id: uuid.UUID,
    entry_id: uuid.UUID,
    created_at: datetime,
    user_id: uuid.UUID | None = None,
) -> None:
    """
    Write initial values for all readonly property schemas in *database_id*
    that belong to the freshly created entry *entry_id*.

    Called once immediately after entry creation, inside the same transaction.

    Readonly types handled
    ----------------------
    id               – next sequential integer within the schema (config.next_id).
                       The config is patched (+1) in the same transaction.
    created_by       – current application username.
    created_time     – the Block.created_at timestamp (ISO-8601, UTC).
    last_edited_by   – same as created_by at creation time.
    last_edited_time – same as created_time at creation time.
    """
    schemas = repo.list_schemas(db, database_id)
    now_iso = created_at.astimezone(timezone.utc).isoformat()

    for schema in schemas:
        if schema.type == "id":
            config = dict(schema.config or {})
            next_id: int = int(config.get("next_id", 1))
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"id_value": next_id},
            )
            config["next_id"] = next_id + 1
            repo.update_schema(db, schema, config=config)

        elif schema.type == "created_by":
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"user_id": str(user_id)} if user_id else None,
            )

        elif schema.type == "created_time":
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"datetime": now_iso},
            )

        elif schema.type == "last_edited_by":
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"user_id": str(user_id)} if user_id else None,
            )

        elif schema.type == "last_edited_time":
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"datetime": now_iso},
            )


def _refresh_last_edited(
    db: Session,
    database_id: uuid.UUID,
    entry_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> None:
    """
    Update ``last_edited_by`` and ``last_edited_time`` schemas whenever a
    regular value upsert occurs on *entry_id*.

    This is a no-op when neither schema type exists in the database.
    """
    schemas = repo.list_schemas(db, database_id)
    now_iso = datetime.now(timezone.utc).isoformat()

    for schema in schemas:
        if schema.type == "last_edited_by":
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"user_id": str(user_id)} if user_id else None,
            )
        elif schema.type == "last_edited_time":
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"datetime": now_iso},
            )


# ─── Cycle-detection helper ───────────────────────────────────────────────────


def _would_create_cycle(
    db: Session,
    database_id: uuid.UUID,
    proposed_id: uuid.UUID,
    proposed_name: str,
    proposed_type: str,
    proposed_config: Optional[dict],
) -> bool:
    """
    Return True if saving a formula / rollup schema with the proposed
    attributes would introduce a circular dependency.

    The check builds the full dependency graph including the proposed schema
    (replacing the old version if it already exists) and runs Kahn's
    topological sort over it.
    """
    existing = repo.list_schemas(db, database_id)
    # Remove the schema being updated so we replace it with the proposal
    schemas_without: list = [s for s in existing if s.id != proposed_id]

    sentinel = SchemaLike(
        id=proposed_id,
        name=proposed_name,
        type=proposed_type,
        config=proposed_config,
    )
    full_schemas = schemas_without + [sentinel]
    graph = build_dependency_graph(full_schemas)
    return has_any_cycle(graph)


def _validate_computed_config(
    payload_type: str,
    payload_config: Optional[dict],
) -> None:
    """
    Validate formula expression syntax or rollup completeness.

    Raises ``HTTPException(422)`` on any detected issue.
    """
    if payload_type == "formula" and payload_config:
        expression = payload_config.get("expression", "")
        if expression:
            try:
                validate_syntax(expression)
            except FormulaError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Formula syntax error: {exc}",
                ) from exc

    if payload_type == "rollup" and payload_config:
        function = payload_config.get("function", "")
        valid_fns = _ROLLUP_FUNCTIONS
        if function and function not in valid_fns:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown rollup function '{function}'. "
                       f"Valid options: {', '.join(sorted(valid_fns))}",
            )


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _get_database_or_raise(db: Session, database_id: uuid.UUID):
    """
    Return the database block or raise an appropriate HTTP exception.

    Raises
    ------
    HTTPException(404)
        If no block with *database_id* exists.
    HTTPException(409)
        If the block exists but is not of type ``'database'``.
    """
    block = repo.get_block(db, database_id)
    if block is None:
        raise HTTPException(
            status_code=404, detail=f"Database {database_id} not found"
        )
    if block.type != "database":
        raise HTTPException(
            status_code=409,
            detail=f"Block {database_id} is not a database (type: '{block.type}')",
        )
    return block


def _schema_position_after_last(db: Session, database_id: uuid.UUID) -> float:
    """Return a position that places a new schema after all existing ones."""
    schemas = repo.list_schemas(db, database_id)
    if not schemas:
        return 1.0
    return schemas[-1].position + 1.0


def _sync_bilateral_relation(
    db: Session,
    schema,
    entry_id: uuid.UUID,
    new_related_ids: list[str],
    old_related_ids: list[str],
    new_nuances: Optional[dict] = None,
) -> None:
    """
    Maintain the mirror side of a bilateral relation property.

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

    # bilateral_self: the mirror IS this schema — entries of the same property
    # in the same database point back at entry_id symmetrically.
    if direction == "bilateral_self":
        mirror_schema = schema
    else:
        target_db_id_raw = config.get("target_database_id")
        if not target_db_id_raw:
            return

        try:
            target_db_id = uuid.UUID(str(target_db_id_raw))
        except ValueError:
            return

        mirror_name: str = (
            config.get("mirror_property_name") or schema.name
        )

        # Ensure the mirror schema exists in the target database.
        mirror_schema = repo.get_schema_by_name(db, target_db_id, mirror_name)
        if mirror_schema is None:
            _ensure_bilateral_mirror(db, schema, target_db_id, mirror_name)
            mirror_schema = repo.get_schema_by_name(db, target_db_id, mirror_name)
        if mirror_schema is None:
            return

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
            current = list(_extract_related_ids_now(pv.value))
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

    for rid_str in removed:
        try:
            rid = uuid.UUID(rid_str)
        except ValueError:
            continue
        pv = repo.get_value(db, rid, mirror_schema.id)
        if pv and pv.value:
            current = [
                i for i in _extract_related_ids_now(pv.value) if i != entry_id_str
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


def _sync_parent_item(
    db: Session,
    entry_id: uuid.UUID,
    new_parent_ids: list[str],
    old_parent_ids: list[str],
    sub_item_schema,
) -> None:
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
    """
    if sub_item_schema is None:
        return

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


def _would_create_parent_cycle(
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


def _sync_sub_item(
    db: Session,
    entry_id: uuid.UUID,
    new_child_ids: list[str],
    old_child_ids: list[str],
    parent_item_schema,
) -> None:
    """
    Maintain the ``parent_item`` mirror whenever a ``sub_item`` value is written
    directly.

    When entry B's sub_item gains child A:
    * A's ``parent_item`` is set to B (if A had a different parent, that parent
      is cleared first — single-parent policy).

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
    """
    if parent_item_schema is None:
        return

    entry_id_str = str(entry_id)
    added   = set(new_child_ids) - set(old_child_ids)
    removed = set(old_child_ids) - set(new_child_ids)

    # For newly added children: set their parent_item to this entry,
    # clearing any previous parent first (single-parent policy).
    for cid_str in added:
        try:
            cid = uuid.UUID(cid_str)
        except ValueError:
            continue
        repo.upsert_value(
            db,
            page_id=cid,
            schema_id=parent_item_schema.id,
            value={"related_ids": [entry_id_str]},
        )

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


def _ensure_bilateral_mirror(
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


# ─── Timeline helpers ─────────────────────────────────────────────────────────


def _parse_pool_range(range_str: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parse a pool range string into ``(start, end)`` ISO timestamp strings.

    ``None`` on either side means open (−∞ or +∞).

    Examples::

        "2024-01-01T00:00:00→2024-12-31T23:59:59"  →  ("2024-01-01T00:00:00", "2024-12-31T23:59:59")
        "2025-01-01T00:00:00→"                      →  ("2025-01-01T00:00:00", None)
        "→2023-12-31T23:59:59"                      →  (None, "2023-12-31T23:59:59")
        ""                                           →  (None, None)
    """
    if "→" in range_str:
        parts = range_str.split("→", 1)
        start = parts[0].strip() or None
        end = parts[1].strip() or None
    else:
        # No arrow: treat the whole string as a start (open-ended "since").
        start = range_str.strip() or None
        end = None
    return start, end


def _pool_to_timeline(pool: dict, nuance_pool: Optional[dict] = None) -> dict:
    """
    Compute the ``_timeline`` dict from a ``relationPool``.

    Handles three range formats in the pool:

    * ``""`` (always-valid) – the UUID is linked for all time.
    * ``"start→"`` / ``"start→end"`` / ``"→end"`` – bounded ranges.

    Rules
    -----
    * When **only** always-valid ranges exist the result is a single ``""``
      slot (the only sentinel allowed as a sole entry in ``_timeline``).
    * When bounded ranges exist, always-valid UIDs are merged into every
      computed slot.  The ``""`` sentinel is never emitted alongside bounded
      slots (the spec prohibits mixing).
    * Invalid or unparseable timestamps are skipped silently.

    Nuance
    ------
    ``nuance_pool`` is an optional ``{ uid: { range_str: label } }`` map that
    runs parallel to *pool*.  When supplied, each computed slot gains a
    ``nuances`` sub-dict mapping the UIDs active in that slot to their nuance
    label for the matching range.  A UID active via its always-valid (``""``)
    range carries the label registered under ``""``; if the same UID is also
    bounded with a different label, the always-valid label wins (mirroring the
    related_ids dedup, which lists the always-valid UID first).  UIDs with no
    label are omitted, and a slot with no nuanced UID carries no ``nuances``
    key — so the output is byte-identical to the nuance-free form whenever
    *nuance_pool* is empty.
    """
    from datetime import datetime as _DT, timedelta as _TD

    if not pool:
        return {}

    np_map: dict = nuance_pool or {}

    def _nuance_for(uid: str, range_str: str) -> Optional[str]:
        ranges = np_map.get(uid)
        if not isinstance(ranges, dict):
            return None
        label = ranges.get(range_str)
        return label or None

    def _parse_ts(ts: str) -> Optional[_DT]:
        try:
            return _DT.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    def _fmt_ts(dt: _DT) -> str:
        # NOTE: strftime("%Y") does not zero-pad years < 1000 on glibc, so a
        # date like year 61 would render as "61-..." instead of "0061-...".
        # The sweepline mixes these derived change-points with the raw,
        # 4-digit-padded client timestamps and orders them by plain string
        # comparison; an unpadded year breaks that ordering ("61-..." sorts
        # after "2020-...") and corrupts every slot boundary. Format the
        # components explicitly to guarantee a fixed-width, zero-padded year.
        return (
            f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
            f"T{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
        )

    # ── Separate always-valid from time-bounded ranges ────────────────────────
    always_uids: list[str] = []
    always_nuance: dict[str, str] = {}
    parsed: list[tuple[str, Optional[str], Optional[str], str]] = []

    for uid, uid_ranges in pool.items():
        for r in (uid_ranges or []):
            if r == "":
                if uid not in always_uids:
                    always_uids.append(uid)
                lbl = _nuance_for(uid, "")
                if lbl and uid not in always_nuance:
                    always_nuance[uid] = lbl
            else:
                s, e = _parse_pool_range(r)
                # Skip entries whose timestamps cannot be parsed
                if s is not None and _parse_ts(s) is None:
                    continue
                if e is not None and _parse_ts(e) is None:
                    continue
                parsed.append((uid, s, e, r))

    def _build_slot(bounded_pairs: list[tuple[str, str]]) -> dict:
        """Assemble a slot dict (related_ids + optional nuances) for a set of
        (uid, range_str) pairs, with always-valid UIDs merged in front."""
        bounded_uids: list[str] = []
        for uid, _r in bounded_pairs:
            if uid not in bounded_uids:
                bounded_uids.append(uid)
        related = always_uids + [u for u in bounded_uids if u not in always_uids]
        nuances: dict[str, str] = dict(always_nuance)
        for uid, r in bounded_pairs:
            if uid in nuances:
                continue
            lbl = _nuance_for(uid, r)
            if lbl:
                nuances[uid] = lbl
        slot: dict = {"related_ids": related}
        if nuances:
            slot["nuances"] = nuances
        return slot

    # ── No bounded ranges → single always-valid slot (or empty) ──────────────
    if not parsed:
        if always_uids:
            slot: dict = {"related_ids": list(always_uids)}
            if always_nuance:
                slot["nuances"] = dict(always_nuance)
            return {"": slot}
        return {}

    # ── Sweepline over bounded ranges ─────────────────────────────────────────
    change_points: set[str] = set()
    for _uid, s, e, _r in parsed:
        if s:
            change_points.add(s)
        if e:
            dt_e = _parse_ts(e)
            if dt_e is not None:
                change_points.add(_fmt_ts(dt_e + _TD(seconds=1)))

    if not change_points:
        if always_uids:
            slot = {"related_ids": list(always_uids)}
            if always_nuance:
                slot["nuances"] = dict(always_nuance)
            return {"": slot}
        return {}

    sorted_cps = sorted(change_points)

    def _bounded_active_at(ts: str) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for uid, s, e, r in parsed:
            if s is not None and ts < s:
                continue
            if e is not None and ts > e:
                continue
            result.append((uid, r))
        return result

    timeline: dict = {}

    # Handle "until" ranges (start=None) — active before the first change-point
    has_until = any(s is None for _uid, s, e, _r in parsed)
    if has_until and sorted_cps:
        first_cp = sorted_cps[0]
        dt_first = _parse_ts(first_cp)
        if dt_first is not None:
            before_end = _fmt_ts(dt_first - _TD(seconds=1))
            before_pairs = [
                (uid, r) for uid, s, e, r in parsed
                if s is None and (e is None or e >= before_end)
            ]
            slot = _build_slot(before_pairs)
            if slot["related_ids"]:
                timeline[f"→{before_end}"] = slot

    n = len(sorted_cps)
    for i, cp in enumerate(sorted_cps):
        bounded_pairs = _bounded_active_at(cp)
        slot = _build_slot(bounded_pairs)
        if not slot["related_ids"]:
            continue

        if i < n - 1:
            next_cp = sorted_cps[i + 1]
            dt_next = _parse_ts(next_cp)
            if dt_next is None:
                continue
            slot_end = _fmt_ts(dt_next - _TD(seconds=1))
            slot_key = f"{cp}→{slot_end}"
        else:
            # Last change-point: open if any bounded-active uid has no end,
            # or if there are always-valid uids (they never end).
            bounded_uids_now = {u for u, _ in bounded_pairs}
            still_open = bool(always_uids) or any(
                e is None
                for uid, s, e, _r in parsed
                if uid in bounded_uids_now and (s is None or s <= cp)
            )
            slot_key = f"{cp}→" if still_open else f"{cp}→{cp}"

        timeline[slot_key] = slot

    return timeline


def _sanitize_nuance_pool(raw: object, pool: dict) -> dict:
    """
    Coerce a client-supplied ``nuancePool`` into ``{ uid: { range_str: label } }``.

    Only (uid, range) pairs that actually exist in *pool* and carry a non-empty
    string label are kept.  Returns ``{}`` when nothing valid remains, so the
    caller can omit the key entirely.
    """
    if not isinstance(raw, dict):
        return {}
    valid_pairs = {
        (uid, r) for uid, ranges in pool.items() for r in (ranges or [])
    }
    out: dict = {}
    for uid, ranges in raw.items():
        if not isinstance(ranges, dict):
            continue
        for r, label in ranges.items():
            if (uid, r) not in valid_pairs:
                continue
            if not isinstance(label, str) or not label.strip():
                continue
            out.setdefault(uid, {})[r] = label
    return out


def _sanitize_flat_nuances(raw: object, related_ids: list[str]) -> dict:
    """
    Coerce a client-supplied flat ``nuances`` map into ``{ uid: label }``.

    Only UIDs present in *related_ids* with a non-empty string label are kept.
    """
    if not isinstance(raw, dict):
        return {}
    allowed = set(related_ids)
    out: dict = {}
    for uid, label in raw.items():
        if uid not in allowed:
            continue
        if not isinstance(label, str) or not label.strip():
            continue
        out[uid] = label
    return out


_TS_RE_SIMPLE = None  # lazy-compiled below


def _is_valid_ts(ts: str) -> bool:
    """Return True if *ts* looks like a valid ISO 8601 datetime string."""
    import re
    global _TS_RE_SIMPLE
    if _TS_RE_SIMPLE is None:
        _TS_RE_SIMPLE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?")
    return bool(_TS_RE_SIMPLE.match(ts))


def _validate_timeline_value(value: dict) -> Optional[str]:
    """
    Validate the structure of a ``_timeline``-wrapped property value.

    Returns an error message string on failure, or ``None`` on success.

    Rules enforced
    --------------
    * ``_timeline`` must be a dict.
    * The empty-string key ``""`` is only permitted as the sole entry.
    * At most one ``"→date"`` (until) key, only as the first entry.
    * At most one ``"date→"`` (since) key, only as the last entry.
    * All other keys must be ``"date→date"`` with valid ISO timestamps.
    * No overlapping ranges (end[i] >= start[i+1] is a violation).
    """
    timeline = value.get("_timeline")
    if not isinstance(timeline, dict):
        return "_timeline must be a JSON object"

    keys = list(timeline.keys())
    if not keys:
        return None  # empty timeline is valid

    # Special case: single "" entry
    if "" in keys:
        if len(keys) != 1:
            return "The '' (always-valid) key is only permitted as the sole timeline entry"
        return None

    # Parse all keys
    parsed: list[tuple[str, Optional[str], Optional[str]]] = []  # (key, start, end)
    for k in keys:
        s, e = _parse_pool_range(k)
        # Validate individual timestamps
        for ts in (s, e):
            if ts is not None and not _is_valid_ts(ts):
                return f"Invalid timestamp in timeline key '{k}': '{ts}'"
        parsed.append((k, s, e))

    # Sort by start (None = −∞ sorts first via empty string proxy)
    def _sort_key(item: tuple) -> str:
        s = item[1]
        return s if s is not None else ""

    parsed.sort(key=_sort_key)

    n = len(parsed)
    for i, (k, s, e) in enumerate(parsed):
        is_first = i == 0
        is_last = i == n - 1

        # "→date" (until): only allowed as first entry
        if s is None and e is not None and not is_first:
            return f"'→{e}' (until-range) is only allowed as the oldest (first) entry"

        # "date→" (since): only allowed as last entry
        if s is not None and e is None and not is_last:
            return f"'{s}→' (since-range) is only allowed as the youngest (last) entry"

        # Overlap check against the next entry
        if not is_last:
            _k2, s2, _e2 = parsed[i + 1]
            e_cmp = e if e is not None else "\xff" * 30  # +∞ sentinel
            s2_cmp = s2 if s2 is not None else ""
            if e_cmp >= s2_cmp:
                return f"Timeline ranges overlap: '{k}' and '{_k2}'"

    return None


def _extract_related_ids_now(value: Optional[dict]) -> list[str]:
    """
    Extract the current flat ``related_ids`` list from a relation value.

    For timeline relations (``_timeline`` present): returns the list from the
    chronologically last slot.  For plain relations: returns ``related_ids``
    from the root dict.  Always returns a plain Python list of strings.
    """
    if not value:
        return []
    if "_timeline" in value:
        # Import here to avoid circular dependency risk
        from app.blocks.computed import _last_timeline_slot
        slot = _last_timeline_slot(value.get("_timeline") or {})
        if slot is None:
            return []
        return list(slot.get("related_ids", []))
    return list(value.get("related_ids", []))


def _migrate_relation_values_to_timeline(
    db: Session,
    database_id: uuid.UUID,
    schema_id: uuid.UUID,
) -> None:
    """
    Migrate flat ``{ related_ids: [...] }`` relation values to the timeline
    pool format when ``hasTimeline`` is enabled on an existing relation schema.

    For each active entry in *database_id* that has a value for *schema_id*
    in the old flat format (``related_ids`` present, ``relationPool`` absent),
    the value is converted to::

        {
            "relationPool": { uid: [""] for uid in related_ids },
            "_timeline":    { "": { "related_ids": [...] } },
        }

    All previously linked entries become permanently "always valid" (``""``
    range), matching the ``migrateWarning`` shown in the UI before the user
    confirms the toggle.

    Values that are already in pool format, are ``None``, or have an empty
    ``related_ids`` list are left untouched.

    Parameters
    ----------
    db:
        Active database session (within the caller's transaction).
    database_id:
        UUID of the database whose entries should be scanned.
    schema_id:
        UUID of the relation schema that was just enabled for timeline.
    """
    entries = repo.list_children(
        db, database_id, state="active",
        exclude_types=frozenset({"entry_template"}),
    )
    for entry in entries:
        pv = repo.get_value(db, entry.id, schema_id)
        if pv is None or not pv.value:
            continue
        val = pv.value
        # Skip values already in pool/timeline format.
        if "relationPool" in val or "_timeline" in val:
            continue
        related_ids: list[str] = list(val.get("related_ids") or [])
        if not related_ids:
            continue
        flat_nuances = _sanitize_flat_nuances(val.get("nuances"), related_ids)
        pool = {uid: [""] for uid in related_ids}
        nuance_pool = {
            uid: {"": flat_nuances[uid]}
            for uid in related_ids
            if flat_nuances.get(uid)
        }
        timeline = _pool_to_timeline(pool, nuance_pool)
        value: dict = {"relationPool": pool}
        if nuance_pool:
            value["nuancePool"] = nuance_pool
        value["_timeline"] = timeline
        repo.upsert_value(
            db,
            page_id=entry.id,
            schema_id=schema_id,
            value=value,
        )


def _get_mirror_schema(db: Session, schema) -> Optional[object]:
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


def _sync_bilateral_relation_timeline(
    db: Session,
    schema,
    mirror_schema,
    entry_id: uuid.UUID,
    new_pool: dict,
    old_pool: dict,
    new_nuance_pool: Optional[dict] = None,
) -> None:
    """
    Synchronise the mirror side of a bilateral timeline relation.

    Computes the diff between *old_pool* and *new_pool* (per UUID × range
    pairs), and for each added/removed pair updates the mirror entry's pool
    accordingly.  After each pool change the mirror's ``_timeline`` is
    recomputed via :func:`_pool_to_timeline`.

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
    if mirror_schema is None:
        return

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
        new_timeline = _pool_to_timeline(current_pool, current_nuance)
        value: dict = {"relationPool": current_pool}
        if current_nuance:
            value["nuancePool"] = current_nuance
        value["_timeline"] = new_timeline
        repo.upsert_value(db, page_id=uid, schema_id=mirror_schema.id, value=value)

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
            new_timeline = _pool_to_timeline(current_pool, current_nuance)
            value = {"relationPool": current_pool}
            if current_nuance:
                value["nuancePool"] = current_nuance
            value["_timeline"] = new_timeline
            repo.upsert_value(db, page_id=uid, schema_id=mirror_schema.id, value=value)
        else:
            repo.upsert_value(db, page_id=uid, schema_id=mirror_schema.id, value=None)


# ─── Database list endpoint ───────────────────────────────────────────────────


@database_router.get(
    "",
    response_model=list[DatabaseListResponse],
)
def list_databases(
    db: Session = Depends(get_db),
    _session: uuid.UUID = Depends(require_session),
):
    """
    Return all active database blocks as lightweight descriptors (id + title).

    Used by the frontend to populate the target-database picker in the
    relation property settings modal.
    """
    blocks = repo.list_databases(db)
    return [
        DatabaseListResponse(
            id=b.id,
            title=(b.content or {}).get("title"),
        )
        for b in blocks
    ]


# ─── Schema endpoints ─────────────────────────────────────────────────────────


@database_router.get(
    "/{database_id}/schemas",
    response_model=list[SchemaResponse],
)
def list_schemas(
    database_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: uuid.UUID = Depends(require_session),
):
    """Return all property schemas for a database, ordered by position."""
    _get_database_or_raise(db, database_id)
    return repo.list_schemas(db, database_id)


@database_router.post(
    "/{database_id}/schemas",
    response_model=SchemaResponse,
    status_code=201,
)
async def create_schema(
    database_id: uuid.UUID,
    payload: SchemaCreate,
    db: Session = Depends(get_db),
    _session: uuid.UUID = Depends(require_session),
):
    """
    Add a new property schema to a database.

    If ``position`` is omitted the schema is appended after the last existing
    one. Returns 409 if a schema with the same name already exists in this
    database. Returns 422 if a formula / rollup config would create a cycle or
    contains a syntax error.

    For bilateral relation schemas, the mirror schema is created eagerly in the
    target database.  A ``database_schema_updated`` WebSocket event is broadcast
    to the target database so open table views can refresh their schema lists
    immediately (and hide the new property automatically).
    """
    _get_database_or_raise(db, database_id)
    position = (
        payload.position
        if payload.position is not None
        else _schema_position_after_last(db, database_id)
    )

    if payload.type in ("formula", "rollup"):
        _validate_computed_config(payload.type, payload.config)
        proposed_id = uuid.uuid4()
        if _would_create_cycle(
            db, database_id, proposed_id,
            payload.name, payload.type, payload.config,
        ):
            raise HTTPException(
                status_code=422,
                detail="This formula / rollup creates a circular dependency",
            )

    # Track whether a mirror schema was created so we know whether to
    # broadcast a schema-update event to the target database after commit.
    mirror_created_in: uuid.UUID | None = None

    try:
        schema = repo.create_schema(
            db,
            database_id=database_id,
            name=payload.name,
            type=payload.type,
            position=position,
            config=payload.config,
        )
        schema.group = payload.group

        # Eagerly create the mirror schema for bilateral relations so it exists
        # immediately in the target database — before any entry is linked.
        if (
            payload.type == "relation"
            and (payload.config or {}).get("direction") == "bilateral"
        ):
            cfg = payload.config or {}
            target_db_id_raw = cfg.get("target_database_id")
            mirror_name_raw = cfg.get("mirror_property_name")
            if target_db_id_raw and mirror_name_raw:
                try:
                    target_db_id = uuid.UUID(str(target_db_id_raw))
                except ValueError:
                    target_db_id = None
                if target_db_id:
                    created = _ensure_bilateral_mirror(
                        db, schema, target_db_id, str(mirror_name_raw)
                    )
                    if created:
                        mirror_created_in = target_db_id

        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Property '{payload.name}' already exists in this database",
        )
    except Exception:
        db.rollback()
        raise

    # Notify clients of the target database that a new schema is available so
    # open DatabaseBlock views can refresh their schema list and auto-hide the
    # new mirror property.  Fires after commit so clients fetch committed data.
    if mirror_created_in is not None:
        await broadcast_block_event(
            event_type="database_schema_updated",
            block_id=str(mirror_created_in),
            before=None,
            after={"database_id": str(mirror_created_in)},
        )

    return schema


@database_router.patch(
    "/{database_id}/schemas/{schema_id}",
    response_model=SchemaResponse,
)
def update_schema(
    database_id: uuid.UUID,
    schema_id: uuid.UUID,
    payload: SchemaUpdate,
    db: Session = Depends(get_db),
    _session: uuid.UUID = Depends(require_session),
):
    """
    Update name, type, config, or position of a property schema.

    Only supplied (non-null) fields are changed. Returns 409 if a rename
    would collide with an existing schema name in the same database.
    Returns 422 if a formula / rollup config update would create a cycle or
    contains a syntax error.

    When a formula or rollup schema's config changes, all entries in the
    database are recomputed within the same transaction.
    """
    _get_database_or_raise(db, database_id)
    schema = repo.get_schema(db, schema_id)
    if schema is None or schema.database_id != database_id:
        raise HTTPException(
            status_code=404,
            detail=f"Schema {schema_id} not found in database {database_id}",
        )

    # Capture pre-update values needed for bilateral relation side-effects.
    old_name = schema.name
    old_config = dict(schema.config or {})
    old_mirror_name: str | None = old_config.get("mirror_property_name")

    effective_type = payload.type if payload.type is not None else schema.type
    effective_config = payload.config if payload.config is not None else schema.config
    effective_name = payload.name if payload.name is not None else schema.name

    if effective_type in ("formula", "rollup") and payload.config is not None:
        _validate_computed_config(effective_type, effective_config)
        if _would_create_cycle(
            db, database_id, schema_id,
            effective_name, effective_type, effective_config,
        ):
            raise HTTPException(
                status_code=422,
                detail="This formula / rollup creates a circular dependency",
            )

    try:
        updated = repo.update_schema(
            db,
            schema,
            name=payload.name,
            type=payload.type,
            config=payload.config,
            position=payload.position,
        )
        if payload.group is not None:
            updated.group = payload.group

        # ── Formula prop() reference rename ──────────────────────────────────
        #
        # Formulas reference properties by name (prop("Name")), so renaming a
        # property would orphan every prop("OldName") reference: it silently
        # resolves to empty and the dependent formula column goes blank.
        # Mirroring the bilateral mirror-name back-pointer handling below,
        # rewrite the references in every formula schema of this database to the
        # new name. Rollups are unaffected — they reference their columns by ID,
        # not by name. Runs before the recompute pass so the corrected
        # expressions are the ones evaluated.
        rewrote_formula = False
        if payload.name is not None and updated.name != old_name:
            for sch in repo.list_schemas(db, database_id):
                if sch.type != "formula":
                    continue
                cfg = dict(sch.config or {})
                expr = cfg.get("expression")
                if not isinstance(expr, str) or not expr:
                    continue
                new_expr = rename_prop_in_expression(expr, old_name, updated.name)
                if new_expr != expr:
                    cfg["expression"] = new_expr
                    repo.update_schema(db, sch, config=cfg)
                    rewrote_formula = True

        # Re-evaluate all entries when the edited schema's computed config
        # changes, or when a rename rewrote prop() references in dependent
        # formulas (so those columns refill with the corrected references).
        if (
            effective_type in ("formula", "rollup") and payload.config is not None
        ) or rewrote_formula:
            entries = repo.list_children(
                db, database_id, state="active",
                exclude_types=frozenset({"entry_template"}),
            )
            for entry in entries:
                compute_all_for_entry(db, database_id, entry.id)

        # ── Bilateral relation side-effects ──────────────────────────────────
        #
        # When a bilateral relation schema is updated two cascading changes may
        # be needed in the target database:
        #
        # A) mirror_property_name changed  →  rename the mirror schema in the
        #    target database so the existing data (related_ids values) is kept.
        #
        # B) own name changed  →  update the mirror schema's back-pointer
        #    (config.mirror_property_name) so it still resolves to this schema.
        #
        # Both are performed inside the same transaction.
        if updated.type == "relation":
            new_config = updated.config or {}
            if new_config.get("direction") == "bilateral":
                target_db_id_raw = new_config.get("target_database_id") or old_config.get("target_database_id")
                new_mirror_name: str | None = new_config.get("mirror_property_name")

                if target_db_id_raw:
                    try:
                        target_db_id = uuid.UUID(str(target_db_id_raw))
                    except ValueError:
                        target_db_id = None

                    if target_db_id:
                        # Case A: mirror_property_name was renamed.
                        if (
                            payload.config is not None
                            and old_mirror_name
                            and new_mirror_name
                            and old_mirror_name != new_mirror_name
                        ):
                            old_mirror = repo.get_schema_by_name(db, target_db_id, old_mirror_name)
                            if old_mirror is not None:
                                repo.update_schema(db, old_mirror, name=new_mirror_name)

                        # Case B: source schema itself was renamed — update
                        # the mirror's back-pointer so the inverse lookup works.
                        if payload.name is not None and payload.name != old_name:
                            # Mirror is identified by its current name (possibly
                            # already renamed in Case A above).
                            current_mirror_name = new_mirror_name or old_mirror_name
                            if current_mirror_name:
                                mirror = repo.get_schema_by_name(db, target_db_id, current_mirror_name)
                                if mirror is not None:
                                    mirror_cfg = dict(mirror.config or {})
                                    mirror_cfg["mirror_property_name"] = updated.name
                                    repo.update_schema(db, mirror, config=mirror_cfg)

                        # Case C: hasTimeline toggled → propagate to mirror schema
                        # so both sides of the relation use the same data model.
                        old_has_timeline = old_config.get("hasTimeline", False)
                        new_has_timeline = new_config.get("hasTimeline", False)
                        if payload.config is not None and old_has_timeline != new_has_timeline:
                            current_mirror_name = new_mirror_name or old_mirror_name
                            if current_mirror_name:
                                mirror = repo.get_schema_by_name(db, target_db_id, current_mirror_name)
                                if mirror is not None:
                                    mirror_cfg = dict(mirror.config or {})
                                    mirror_cfg["hasTimeline"] = new_has_timeline
                                    repo.update_schema(db, mirror, config=mirror_cfg)

                        # Case D: nuance config changed → propagate to mirror schema.
                        # Each side stores its own affixes/orientation at the top
                        # level; the option set is shared.  The synced sub-object
                        # holds the *other* side's framing, so when writing the
                        # mirror we swap: the source's ``synced`` becomes the
                        # mirror's own top-level framing, and the source's own
                        # framing becomes the mirror's ``synced`` (so reopening the
                        # modal on either side shows both sides correctly).
                        if payload.config is not None and old_config.get("nuance") != new_config.get("nuance"):
                            current_mirror_name = new_mirror_name or old_mirror_name
                            if current_mirror_name:
                                mirror = repo.get_schema_by_name(db, target_db_id, current_mirror_name)
                                if mirror is not None:
                                    src_nuance = new_config.get("nuance") or {}
                                    mirror_cfg = dict(mirror.config or {})
                                    if src_nuance.get("enabled"):
                                        synced = src_nuance.get("synced") or {}
                                        mirror_cfg["nuance"] = {
                                            "enabled": True,
                                            "options": src_nuance.get("options") or [],
                                            "affix1": synced.get("affix1", ""),
                                            "affix2": synced.get("affix2", ""),
                                            "orientation": synced.get("orientation", "prepended"),
                                            "synced": {
                                                "affix1": src_nuance.get("affix1", ""),
                                                "affix2": src_nuance.get("affix2", ""),
                                                "orientation": src_nuance.get("orientation", "prepended"),
                                            },
                                        }
                                    else:
                                        mirror_cfg["nuance"] = {"enabled": False}
                                    repo.update_schema(db, mirror, config=mirror_cfg)

                        # Ensure the mirror schema exists (covers the edge case
                        # where config was updated from unilateral → bilateral).
                        current_mirror_name = new_mirror_name or old_mirror_name
                        if current_mirror_name:
                            _ensure_bilateral_mirror(db, updated, target_db_id, current_mirror_name)

        # ── Relation timeline value migration ────────────────────────────────
        #
        # When hasTimeline is newly enabled on a relation schema, existing
        # flat ``{ related_ids: [...] }`` values must be converted to the
        # pool format or they become invisible to the UI (which now expects
        # ``relationPool`` / ``_timeline``).  All linked entries default to
        # the "always valid" (``""``) range, matching the migrateWarning
        # shown in the frontend before the user confirms the toggle.
        #
        # For bilateral relations the mirror schema's values are migrated in
        # the same transaction (Case C above already toggled hasTimeline on
        # the mirror schema config).
        if updated.type == "relation" and payload.config is not None:
            _new_cfg = updated.config or {}
            _old_ht = old_config.get("hasTimeline", False)
            _new_ht = _new_cfg.get("hasTimeline", False)
            if not _old_ht and _new_ht:
                _migrate_relation_values_to_timeline(db, database_id, schema_id)
                _direction = _new_cfg.get("direction")
                if _direction == "bilateral":
                    _target_raw = (
                        _new_cfg.get("target_database_id")
                        or old_config.get("target_database_id")
                    )
                    if _target_raw:
                        try:
                            _target_db_id = uuid.UUID(str(_target_raw))
                        except ValueError:
                            _target_db_id = None
                        if _target_db_id:
                            _mirror_name = (
                                _new_cfg.get("mirror_property_name")
                                or old_config.get("mirror_property_name")
                                or updated.name
                            )
                            _mirror_sch = repo.get_schema_by_name(
                                db, _target_db_id, _mirror_name
                            )
                            if _mirror_sch is not None:
                                _migrate_relation_values_to_timeline(
                                    db, _target_db_id, _mirror_sch.id
                                )

        db.commit()
        return updated
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A property with that name already exists in this database",
        )
    except Exception:
        db.rollback()
        raise


@database_router.delete(
    "/{database_id}/schemas/{schema_id}",
    status_code=204,
)
def delete_schema(
    database_id: uuid.UUID,
    schema_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: uuid.UUID = Depends(require_session),
):
    """
    Delete a property schema and cascade-delete all associated values.

    Returns 204 No Content on success.
    """
    _get_database_or_raise(db, database_id)
    schema = repo.get_schema(db, schema_id)
    if schema is None or schema.database_id != database_id:
        raise HTTPException(
            status_code=404,
            detail=f"Schema {schema_id} not found in database {database_id}",
        )
    try:
        repo.delete_schema(db, schema)
        db.commit()
    except Exception:
        db.rollback()
        raise


# ─── Formula validate endpoint ────────────────────────────────────────────────


@database_router.post(
    "/{database_id}/formulas/validate",
    response_model=FormulaValidateResponse,
)
def validate_formula(
    database_id: uuid.UUID,
    payload: FormulaValidateRequest,
    db: Session = Depends(get_db),
    _session: uuid.UUID = Depends(require_session),
):
    """
    Validate a formula expression for syntax errors and return the list of
    property names it references.

    Used by the PropertySettingsModal to provide live feedback in the formula
    editor.  Does not persist anything.

    Returns
    -------
    FormulaValidateResponse
        ``valid``      – True if the expression parses without errors.
        ``error``      – Human-readable message when ``valid`` is False.
        ``prop_names`` – Property names referenced via ``prop('Name')``.
    """
    _get_database_or_raise(db, database_id)
    from app.blocks.formula_engine import extract_prop_names

    try:
        validate_syntax(payload.expression)
        names = extract_prop_names(payload.expression)
        return FormulaValidateResponse(valid=True, prop_names=names)
    except FormulaError as exc:
        return FormulaValidateResponse(valid=False, error=str(exc))


# ─── Entry endpoints ──────────────────────────────────────────────────────────


@database_router.get(
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
    _get_database_or_raise(db, database_id)
    if not perm_repo.can_user_access(db, database_id, current_user):
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


@database_router.post(
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
    _get_database_or_raise(db, database_id)
    if not perm_repo.can_user_access(db, database_id, current_user):
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


@database_router.post(
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
    Resolve a set of entry IDs to lightweight ``{id, title, database_id}``
    descriptors for rendering relation chips.

    Relation cells store only the target entry IDs.  Resolving their titles
    from the paginated entry listing breaks down once a relation points past
    the active display limit, because the limited page never contains the
    linked entry (#27).  This endpoint loads exactly the requested IDs in a
    single query, independent of any limit, so a chip renders regardless of the
    target database's pagination position of its entry.

    Only active, non-template entries that are direct children of
    ``database_id`` are returned; unknown, trashed or foreign IDs are silently
    omitted, which doubles as the soft-deleted-entry filter the relation cell
    relied on before.

    Permission
    ----------
    Non-admin users that do not have access to the database block receive an
    empty list, mirroring the listing endpoints so relation chips never leak
    titles from databases the user may not see.
    """
    _get_database_or_raise(db, database_id)
    if not perm_repo.can_user_access(db, database_id, current_user):
        return []
    if not payload.ids:
        return []
    blocks = repo.list_blocks_by_ids(
        db,
        payload.ids,
        parent_id=database_id,
        state="active",
        exclude_types=frozenset({"entry_template"}),
    )
    return [
        EntryTitleResponse(
            id=block.id,
            title=(block.content or {}).get("title"),
            database_id=database_id,
        )
        for block in blocks
    ]


@database_router.post(
    "/{database_id}/entries",
    response_model=EntryResponse,
    status_code=201,
)
async def create_entry(
    database_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new page-type entry block inside the database.

    The entry is appended after all existing siblings. Returns the new entry
    with an empty values map.

    After creating the entry, readonly properties are populated and all
    formula / rollup schemas are evaluated (initial values are null /
    empty-context results).
    """
    _get_database_or_raise(db, database_id)
    try:
        block = service.create_block(db, type="page", parent_id=database_id)
        _populate_readonly_properties(db, database_id, block.id, block.created_at, current_user.id)
        compute_all_for_entry(db, database_id, block.id)
        db.commit()
        await broadcast_block_event(
            event_type="created",
            block_id=str(block.id),
            before=None,
            after={
                "id": str(block.id),
                "parent_id": str(database_id),
                "type": "page",
                "position": block.position,
                "state": block.state,
            },
        )
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


@database_router.post(
    "/{database_id}/entries/{entry_id}/duplicate",
    response_model=EntryResponse,
    status_code=201,
)
async def duplicate_entry(
    database_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Duplicate an existing database entry.

    Creates a new page-type block immediately after the original in position
    order and copies over:

    * ``content``  – full content dict (title, cover, etc.)
    * ``icon``     – entry icon
    * ``state``    – block state (e.g. 'active')
    * All writable property values – readonly types (id, created_*, formula,
      rollup) are intentionally excluded so the new entry gets its own
      auto-generated values.

    Bilateral relation properties are mirrored via the same
    ``_sync_bilateral_relation`` helper used by the regular upsert endpoint,
    keeping a single source of truth for that logic.

    The entire operation runs inside a single transaction.  One
    ``database_entries_updated`` broadcast is sent for the source database;
    additional broadcasts are sent for any bilateral relation target databases
    whose mirror values were written.

    Raises 404 if either the database or the original entry is not found.
    """
    _get_database_or_raise(db, database_id)
    original = repo.get_block(db, entry_id)
    if original is None or original.parent_id != database_id:
        raise HTTPException(
            status_code=404,
            detail=f"Entry {entry_id} not found in database {database_id}",
        )

    # Snapshot all property values before opening the write transaction so
    # SQLAlchemy's identity map cannot mix up old and new rows.
    values_map = repo.list_values_for_pages(db, [entry_id])
    original_pvs = values_map.get(entry_id, [])

    # Full schema objects are needed both to filter readonly types and to
    # pass to _sync_bilateral_relation (reuses the existing SSOT helper).
    all_schemas = repo.list_schemas(db, database_id)
    schema_map = {s.id: s for s in all_schemas}

    # Collect target-database IDs for cross-DB bilateral broadcasts.
    bilateral_target_dbs: set[str] = set()

    try:
        new_block = service.create_block(db, type="page", parent_id=database_id)

        # Copy block-level fields from the original.
        new_block.content = dict(original.content) if original.content else None
        new_block.icon = original.icon
        new_block.state = original.state

        # Copy all writable property values and mirror bilateral relations.
        for pv in original_pvs:
            schema = schema_map.get(pv.property_schema_id)
            if schema is None or schema.type in _READONLY_TYPES:
                continue
            if pv.value is None:
                continue

            repo.upsert_value(
                db,
                page_id=new_block.id,
                schema_id=pv.property_schema_id,
                value=pv.value,
            )

            # Keep the mirror side of bilateral relations in sync using the
            # same helpers that upsert_value uses — single source of truth.
            is_bilateral = (
                schema.type == "relation"
                and (schema.config or {}).get("direction") in ("bilateral", "bilateral_self")
            )
            if is_bilateral:
                is_relation_timeline = bool((schema.config or {}).get("hasTimeline", False))
                if is_relation_timeline:
                    new_pool = dict((pv.value or {}).get("relationPool") or {})
                    new_nuance_pool = dict((pv.value or {}).get("nuancePool") or {})
                    mirror_schema = _get_mirror_schema(db, schema)
                    _sync_bilateral_relation_timeline(
                        db, schema, mirror_schema, new_block.id, new_pool, old_pool={},
                        new_nuance_pool=new_nuance_pool,
                    )
                else:
                    new_related_ids = list(_extract_related_ids_now(pv.value))
                    new_flat_nuances = dict((pv.value or {}).get("nuances") or {})
                    # old_related_ids is empty: the duplicate is a fresh entry
                    # with no prior relations on the mirror side.
                    _sync_bilateral_relation(
                        db, schema, new_block.id, new_related_ids, old_related_ids=[],
                        new_nuances=new_flat_nuances,
                    )
                _raw_target = (schema.config or {}).get("target_database_id")
                if _raw_target and str(_raw_target) != str(database_id):
                    bilateral_target_dbs.add(str(_raw_target))

        # Seed readonly properties (id, created_*, last_edited_*) for the
        # new entry — these must differ from the original's values.
        _populate_readonly_properties(db, database_id, new_block.id, new_block.created_at, current_user.id)

        # Re-evaluate formula / rollup schemas with the copied input values.
        compute_all_for_entry(db, database_id, new_block.id)

        # Cascade to sibling entries in the same database and entries in other
        # databases whose rollups reference the new entry.  This matters when
        # bilateral sync added the new entry ID to mirror entries' relation
        # values — those mirrors' rollups must now include the duplicate.
        compute_same_db_rollup_dependents(db, database_id, new_block.id)
        cascade_db_ids = compute_cross_db_dependents(db, database_id, new_block.id)

        db.commit()
        db.refresh(new_block)

        new_values_map = repo.list_values_for_pages(db, [new_block.id])
        new_values: dict[str, Optional[dict]] = {
            str(pv.property_schema_id): pv.value
            for pv in new_values_map.get(new_block.id, [])
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

    # One broadcast for the source database after the full commit.
    await broadcast_block_event(
        event_type="database_entries_updated",
        block_id=str(database_id),
        before=None,
        after={"database_id": str(database_id)},
    )
    # Notify all other affected databases: bilateral relation targets and any
    # databases that had rollup dependants recomputed by the cascade.
    other_dbs = bilateral_target_dbs | {str(d) for d in cascade_db_ids}
    for target_db_id in other_dbs:
        await broadcast_block_event(
            event_type="database_entries_updated",
            block_id=target_db_id,
            before=None,
            after={"database_id": target_db_id},
        )

    return EntryResponse(
        id=new_block.id,
        position=new_block.position,
        content=new_block.content,
        icon=new_block.icon,
        state=new_block.state,
        values=new_values,
    )


@database_router.post(
    "/{database_id}/seed-readonly-schemas",
    response_model=list[SchemaResponse],
    status_code=200,
)
def seed_readonly_schemas(
    database_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: uuid.UUID = Depends(require_session),
):
    """
    Idempotently create the system-managed property schemas for a database
    if they do not already exist.

    Safe to call on every frontend mount — schemas that already exist are
    left unchanged. Only newly created schemas are returned.

    Schemas seeded (in order)
    -------------------------
    id               – auto-incrementing sequential integer
    created_by       – username of the entry creator
    created_time     – ISO-8601 UTC creation timestamp
    last_edited_by   – username of the last editor
    last_edited_time – ISO-8601 UTC last-edit timestamp
    parent_item      – single-parent relation (user-writable)
    sub_item         – backend-managed child list (mirror of parent_item)

    The ``parent_item`` / ``sub_item`` pair is always created together.
    Each schema's ``config.partner_schema_id`` stores the UUID of its
    counterpart so the sync helper can resolve the mirror without a
    full schema scan.
    """
    _get_database_or_raise(db, database_id)
    existing = repo.list_schemas(db, database_id)
    existing_types = {s.type for s in existing}

    readonly_defaults: list[dict] = [
        {"type": "id",               "name": "ID",                "config": {"prefix": "", "next_id": 1}},
        {"type": "created_by",       "name": "Created by",        "config": None},
        {"type": "created_time",     "name": "Created time",      "config": None},
        {"type": "last_edited_by",   "name": "Last edited by",    "config": None},
        {"type": "last_edited_time", "name": "Last edited time",  "config": None},
    ]

    created: list = []
    position = (existing[-1].position + 1.0) if existing else 1.0

    for defn in readonly_defaults:
        if defn["type"] not in existing_types:
            schema = repo.create_schema(
                db,
                database_id=database_id,
                name=defn["name"],
                type=defn["type"],
                position=position,
                config=defn["config"],
            )
            created.append(schema)
            position += 1.0

    # ── Sub-item pair ────────────────────────────────────────────────────────
    # Create parent_item and sub_item together so their partner_schema_id
    # references can be back-filled in a single commit.
    has_parent_item = "parent_item" in existing_types
    has_sub_item    = "sub_item"    in existing_types

    if not has_parent_item and not has_sub_item:
        pi_schema = repo.create_schema(
            db,
            database_id=database_id,
            name="Parent item",
            type="parent_item",
            position=position,
            config={},
        )
        position += 1.0
        si_schema = repo.create_schema(
            db,
            database_id=database_id,
            name="Sub-items",
            type="sub_item",
            position=position,
            config={"partner_schema_id": str(pi_schema.id)},
        )
        position += 1.0
        # Back-fill the partner reference on parent_item now that sub_item has an ID.
        repo.update_schema(db, pi_schema, config={"partner_schema_id": str(si_schema.id)})
        created.extend([pi_schema, si_schema])

    elif not has_parent_item:
        # sub_item already exists without its partner — create parent_item and link.
        si_existing = next((s for s in existing if s.type == "sub_item"), None)
        pi_schema = repo.create_schema(
            db,
            database_id=database_id,
            name="Parent item",
            type="parent_item",
            position=position,
            config={"partner_schema_id": str(si_existing.id)} if si_existing else {},
        )
        created.append(pi_schema)

    elif not has_sub_item:
        # parent_item already exists without its partner — create sub_item and link.
        pi_existing = next((s for s in existing if s.type == "parent_item"), None)
        si_schema = repo.create_schema(
            db,
            database_id=database_id,
            name="Sub-items",
            type="sub_item",
            position=position,
            config={"partner_schema_id": str(pi_existing.id)} if pi_existing else {},
        )
        if pi_existing and not (pi_existing.config or {}).get("partner_schema_id"):
            repo.update_schema(
                db, pi_existing,
                config={"partner_schema_id": str(si_schema.id)},
            )
        created.append(si_schema)

    if created:
        db.commit()
        for s in created:
            db.refresh(s)

    return created


@database_router.put(
    "/{database_id}/entries/{entry_id}/values/{schema_id}",
    status_code=204,
)
async def upsert_value(
    database_id: uuid.UUID,
    entry_id: uuid.UUID,
    schema_id: uuid.UUID,
    payload: ValueUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create or update the property value for a specific entry / schema pair.

    Returns 204 No Content on success. The ``value`` field may be ``null``
    to explicitly clear the cell.

    For ``relation`` schemas with ``config.direction == "bilateral"``, the
    mirror side of the relation is automatically kept in sync within the same
    transaction (see ``_sync_bilateral_relation``).

    After every successful write:
    1. All formula / rollup schemas in this database are re-evaluated for the
       changed entry.
    2. Sibling entries in the same database whose rollup schemas aggregate data
       from this entry via a self-referential relation are also re-evaluated.
    3. Entries in other databases whose rollup schemas pull from this database
       and reference this entry are also re-evaluated (cross-DB cascade).
    4. A ``database_entries_updated`` WebSocket event is broadcast for every
       affected database so open table views refresh automatically.

    Raises 404 if the entry does not belong to this database, or if the
    schema does not belong to this database.
    Raises 422 for schema types that are backend-managed (id, created_*, formula,
    rollup).
    """
    _get_database_or_raise(db, database_id)
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
    if schema.type in _READONLY_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Property '{schema.name}' is system-managed and cannot be set directly.",
        )

    # ── parent_item: single-parent policy + cycle detection ──────────────────
    is_parent_item = schema.type == "parent_item"
    is_sub_item    = schema.type == "sub_item"
    if is_parent_item and payload.value is not None:
        related = payload.value.get("related_ids") or []
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
            # Resolve partner schema for the loop walk.
            partner_id_str = (schema.config or {}).get("partner_schema_id")
            partner_schema = None
            if partner_id_str:
                all_schemas = repo.list_schemas(db, database_id)
                partner_schema = next(
                    (s for s in all_schemas if str(s.id) == partner_id_str), None
                )
            if _would_create_parent_cycle(db, entry_id, proposed_parent_id, schema.id):
                raise HTTPException(
                    status_code=422,
                    detail="Setting this parent would create a cycle in the hierarchy.",
                )

    config = schema.config or {}
    has_timeline = bool(config.get("hasTimeline", False))
    is_relation = schema.type == "relation"
    is_relation_timeline = is_relation and has_timeline

    try:
        # ── Timeline validation ───────────────────────────────────────────────
        if payload.value is not None:
            if is_relation_timeline:
                # For timeline relations: only relationPool writes are accepted.
                # Direct writes to _timeline are rejected.
                if "_timeline" in payload.value and "relationPool" not in payload.value:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "For timeline relations, write to 'relationPool' — "
                            "'_timeline' is computed automatically by the backend."
                        ),
                    )
            elif has_timeline and "_timeline" in payload.value:
                # Non-relation timeline: validate structure
                err = _validate_timeline_value(payload.value)
                if err:
                    raise HTTPException(status_code=422, detail=f"Invalid timeline value: {err}")

        # ── Bilateral relation diff (capture old state before write) ─────────
        old_related_ids: list[str] = []
        old_pool: dict = {}
        _direction = config.get("direction")
        is_bilateral = (
            is_relation and _direction in ("bilateral", "bilateral_self")
        )
        bilateral_target_db_str: str | None = None

        if is_bilateral:
            old_pv = repo.get_value(db, entry_id, schema_id)
            if old_pv and old_pv.value:
                if is_relation_timeline:
                    old_pool = dict(old_pv.value.get("relationPool") or {})
                else:
                    old_related_ids = _extract_related_ids_now(old_pv.value)
            if _direction == "bilateral":
                _raw_target = config.get("target_database_id")
                if _raw_target and str(_raw_target) != str(database_id):
                    bilateral_target_db_str = str(_raw_target)

        # ── parent_item: capture old state and resolve sub_item partner ───────
        old_parent_ids: list[str] = []
        sub_item_schema_ref = None
        if is_parent_item:
            old_pv2 = repo.get_value(db, entry_id, schema_id)
            if old_pv2 and old_pv2.value:
                old_parent_ids = list(old_pv2.value.get("related_ids") or [])
            partner_id_str = config.get("partner_schema_id")
            if partner_id_str:
                all_schemas = repo.list_schemas(db, database_id)
                sub_item_schema_ref = next(
                    (s for s in all_schemas if str(s.id) == partner_id_str), None
                )

        # ── sub_item: capture old state and resolve parent_item partner ───────
        old_child_ids: list[str] = []
        parent_item_schema_ref = None
        if is_sub_item:
            old_pv3 = repo.get_value(db, entry_id, schema_id)
            if old_pv3 and old_pv3.value:
                old_child_ids = list(old_pv3.value.get("related_ids") or [])
            partner_id_str_si = config.get("partner_schema_id")
            if partner_id_str_si:
                all_schemas_si = repo.list_schemas(db, database_id)
                parent_item_schema_ref = next(
                    (s for s in all_schemas_si if str(s.id) == partner_id_str_si), None
                )

        # ── Build the value to store ─────────────────────────────────────────
        stored_value = payload.value

        if is_relation_timeline and payload.value is not None:
            new_pool = dict(payload.value.get("relationPool") or {})
            new_nuance_pool = _sanitize_nuance_pool(
                payload.value.get("nuancePool"), new_pool
            )
            computed_timeline = _pool_to_timeline(new_pool, new_nuance_pool)
            stored_value = {"relationPool": new_pool}
            if new_nuance_pool:
                stored_value["nuancePool"] = new_nuance_pool
            stored_value["_timeline"] = computed_timeline
        elif is_relation and payload.value is not None:
            # Flat relation: keep related_ids, sanitise the optional per-uid
            # nuance map so unknown uids / blank labels never reach storage.
            new_related_ids = list(payload.value.get("related_ids") or [])
            flat_nuances = _sanitize_flat_nuances(
                payload.value.get("nuances"), new_related_ids
            )
            stored_value = {"related_ids": new_related_ids}
            if flat_nuances:
                stored_value["nuances"] = flat_nuances

        repo.upsert_value(
            db, page_id=entry_id, schema_id=schema_id, value=stored_value
        )

        # ── Bilateral sync ───────────────────────────────────────────────────
        # Suppressed for entry_template blocks: templates store relation values
        # for later Apply but must not write mirror sides — the template block
        # itself is not a real participant in any relation graph.
        if is_bilateral and entry.type != "entry_template":
            if is_relation_timeline:
                new_pool = dict((stored_value or {}).get("relationPool") or {})
                new_nuance_pool = dict((stored_value or {}).get("nuancePool") or {})
                mirror_schema = _get_mirror_schema(db, schema)
                _sync_bilateral_relation_timeline(
                    db, schema, mirror_schema, entry_id, new_pool, old_pool,
                    new_nuance_pool=new_nuance_pool,
                )
            else:
                new_related_ids = _extract_related_ids_now(stored_value)
                new_flat_nuances = dict((stored_value or {}).get("nuances") or {})
                _sync_bilateral_relation(
                    db, schema, entry_id, new_related_ids, old_related_ids,
                    new_nuances=new_flat_nuances,
                )

        # ── parent_item sync ─────────────────────────────────────────────────
        # Suppressed for entry_template blocks for the same reason: setting a
        # parent_item value on a template must not register the template block
        # as a sub-item on the referenced parent entry.
        if is_parent_item and entry.type != "entry_template":
            new_parent_ids = list((stored_value or {}).get("related_ids") or [])
            _sync_parent_item(
                db, entry_id, new_parent_ids, old_parent_ids, sub_item_schema_ref
            )

        # ── sub_item sync ────────────────────────────────────────────────────
        # When sub_item is written directly, keep the parent_item mirror in
        # sync. Suppressed for entry_template blocks (same rationale as above).
        if is_sub_item and entry.type != "entry_template":
            new_child_ids = list((stored_value or {}).get("related_ids") or [])
            _sync_sub_item(
                db, entry_id, new_child_ids, old_child_ids, parent_item_schema_ref
            )

        _refresh_last_edited(db, database_id, entry_id, current_user.id)

        # Re-evaluate local formula / rollup schemas for this entry.
        compute_all_for_entry(db, database_id, entry_id)

        # Re-evaluate sibling entries in this database that have a rollup
        # aggregating the changed entry via a self-referential relation.
        compute_same_db_rollup_dependents(db, database_id, entry_id)

        # Re-evaluate entries in other databases that depend on this entry
        # via rollup schemas (cross-DB cascade).
        affected_db_ids = compute_cross_db_dependents(db, database_id, entry_id)

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    # Broadcast database_entries_updated for this DB and all cascade-affected DBs.
    # Fires after commit so clients always fetch committed data.
    await broadcast_block_event(
        event_type="database_entries_updated",
        block_id=str(database_id),
        before=None,
        after={"database_id": str(database_id)},
    )
    for affected_id in affected_db_ids:
        await broadcast_block_event(
            event_type="database_entries_updated",
            block_id=str(affected_id),
            before=None,
            after={"database_id": str(affected_id)},
        )

    # For bilateral relations the mirror entries in the target database were
    # updated server-side but the target database's clients have not been
    # notified yet.  Broadcast separately so open DatabaseBlock views for the
    # target database refresh immediately without requiring a full page reload.
    if bilateral_target_db_str:
        await broadcast_block_event(
            event_type="database_entries_updated",
            block_id=bilateral_target_db_str,
            before=None,
            after={"database_id": bilateral_target_db_str},
        )

    # Notify the automation engine.  Fires after all broadcasts so that any
    # secondary updates triggered by automations arrive after the primary one.
    # Failures are logged but never propagate to the caller — a broken
    # automation must not surface as a request error.
    try:
        await automation_receive(
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


# ─── Entry-template endpoints ─────────────────────────────────────────────────


class EntryTemplateResponse(BaseModel):
    """A database entry template block with its property values."""
    id: uuid.UUID
    position: float
    content: Optional[dict]
    icon: Optional[str]
    values: dict[str, Optional[dict]]


@database_router.post(
    "/{database_id}/entry-templates",
    response_model=EntryTemplateResponse,
    status_code=201,
)
async def create_entry_template(
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
    _get_database_or_raise(db, database_id)
    try:
        block = service.create_block(db, type="entry_template", parent_id=database_id)
        _populate_readonly_properties(db, database_id, block.id, block.created_at, current_user.id)
        compute_all_for_entry(db, database_id, block.id)
        db.commit()
        await broadcast_block_event(
            event_type="database_entries_updated",
            block_id=str(database_id),
            before=None,
            after={"database_id": str(database_id)},
        )
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


@database_router.get(
    "/{database_id}/entry-templates",
    response_model=list[EntryTemplateResponse],
)
def list_entry_templates(
    database_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: uuid.UUID = Depends(require_session),
):
    """
    Return all active entry templates belonging to *database_id*.

    Values are loaded in a single additional query (no N+1), identical to
    the regular ``GET /entries`` endpoint.
    """
    _get_database_or_raise(db, database_id)
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


@database_router.post(
    "/{database_id}/entry-templates/{template_id}/apply/{entry_id}",
    status_code=204,
)
async def apply_entry_template(
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
    are applied using the same helpers as the regular upsert endpoint.

    Returns 204 No Content on success.
    Returns 404 if the database, template, or target entry is not found,
    or if the template / entry do not belong to the given database.
    """
    _get_database_or_raise(db, database_id)

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

    values_map = repo.list_values_for_pages(db, [template_id])
    template_pvs = values_map.get(template_id, [])
    all_schemas = repo.list_schemas(db, database_id)
    schema_map = {s.id: s for s in all_schemas}
    bilateral_target_dbs: set[str] = set()

    try:
        # Copy block-level content (title, icon) from the template.
        if template.content:
            target.content = dict(template.content)
        if template.icon:
            target.icon = template.icon
        db.flush()

        # Copy all writable property values from the template.
        for pv in template_pvs:
            schema = schema_map.get(pv.property_schema_id)
            if schema is None or schema.type in _READONLY_TYPES:
                continue
            if pv.value is None:
                continue

            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=pv.property_schema_id,
                value=pv.value,
            )

            # Keep bilateral relation mirrors in sync.
            is_bilateral = (
                schema.type == "relation"
                and (schema.config or {}).get("direction") in ("bilateral", "bilateral_self")
            )
            if is_bilateral:
                is_relation_timeline = bool((schema.config or {}).get("hasTimeline", False))
                if is_relation_timeline:
                    new_pool = dict((pv.value or {}).get("relationPool") or {})
                    new_nuance_pool = dict((pv.value or {}).get("nuancePool") or {})
                    mirror_schema = _get_mirror_schema(db, schema)
                    _sync_bilateral_relation_timeline(
                        db, schema, mirror_schema, entry_id, new_pool, old_pool={},
                        new_nuance_pool=new_nuance_pool,
                    )
                else:
                    new_related_ids = list(_extract_related_ids_now(pv.value))
                    new_flat_nuances = dict((pv.value or {}).get("nuances") or {})
                    _sync_bilateral_relation(
                        db, schema, entry_id, new_related_ids, old_related_ids=[],
                        new_nuances=new_flat_nuances,
                    )
                _raw_target = (schema.config or {}).get("target_database_id")
                if _raw_target and str(_raw_target) != str(database_id):
                    bilateral_target_dbs.add(str(_raw_target))

            # Keep parent_item / sub_item mirrors in sync.
            partner_id_str = (schema.config or {}).get("partner_schema_id")
            partner_schema = None
            if partner_id_str:
                partner_schema = next(
                    (s for s in all_schemas if str(s.id) == partner_id_str), None
                )
            if schema.type == "parent_item":
                new_parent_ids = list(_extract_related_ids_now(pv.value))
                _sync_parent_item(db, entry_id, new_parent_ids, [], partner_schema)
            elif schema.type == "sub_item":
                new_child_ids = list(_extract_related_ids_now(pv.value))
                _sync_sub_item(db, entry_id, new_child_ids, [], partner_schema)

        _refresh_last_edited(db, database_id, entry_id, current_user.id)
        compute_all_for_entry(db, database_id, entry_id)
        compute_same_db_rollup_dependents(db, database_id, entry_id)
        db.commit()

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    await broadcast_block_event(
        event_type="database_entries_updated",
        block_id=str(database_id),
        before=None,
        after={"database_id": str(database_id)},
    )
    for affected_id in bilateral_target_dbs:
        await broadcast_block_event(
            event_type="database_entries_updated",
            block_id=str(affected_id),
            before=None,
            after={"database_id": str(affected_id)},
        )