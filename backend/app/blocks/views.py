"""
Database views.

A view is a saved way of looking at a database: which entries it shows, in
which order, and how they are laid out. Views belong to the database block
and are shared by every user and every placement of that database; each
placement (the database block itself or a ``linked_database`` block) keeps
its own choice of view.

The backend owns views completely. It stores and validates them, keeps them
consistent when properties are added or deleted, and executes them: asked for
a page of a view, it reads the view's filters and sorts itself, runs the
query, and returns the entries together with everything needed to display
their relations. The frontend only edits views and asks for pages.

Structure
---------
``filter_groups`` and ``sorts`` are what the backend executes. ``settings``
is the presentation the frontend applies on top. All three are validated on
every write by the models below, which define the stored shape; keys inside
them use the frontend's camelCase spelling because they are exchanged as is.

Writes are partial: an update names only the fields it changes, and
``settings`` is merged key by key, so two people adjusting different aspects
of the same view at the same time do not overwrite each other. The row is
locked for the read-merge-write of an update.

Default view and system properties
----------------------------------
A database always has at least one view. The first time its views are
requested, the backend creates the system-managed properties if they are
missing and a default view if there is none. The default view has an empty
name, which the frontend displays as its localized default.
"""
import uuid
from typing import Any, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.blocks import options
from app.blocks import repository as repo
from app.blocks import resolution
from app.blocks.models import Block, DatabaseView, DatabaseViewSelection, PropertySchema
from app.permissions import repository as perm_repo
from app.users.model import User

NAME_COL_KEY = "__name__"

VIEW_TYPES: tuple[str, ...] = (
    "table", "calendar", "list", "gallery", "board", "family_tree", "mindmap", "graph",
)

# Properties hidden in a new default view: system-managed values that are
# rarely what a table is for.
READONLY_SCHEMA_TYPES: frozenset[str] = frozenset({
    "id", "created_by", "created_time", "last_edited_by", "last_edited_time",
    "parent_item", "sub_item",
})

# Views that lay entries out on a time axis need all of them at once.
UNPAGED_VIEW_TYPES: frozenset[str] = frozenset({"calendar"})

# Upper bound for a single page, and for an unpaged view.
MAX_PAGE_SIZE = 10_000

FilterOperator = Literal[
    "eq", "neq",
    "contains", "not_contains", "starts_with", "ends_with",
    "is_empty", "is_not_empty",
    "gt", "gte", "lt", "lte",
    "past_week", "past_month", "past_year",
    "this_week", "next_week", "next_month", "next_year",
    "between",
]
AggregationType = Literal["none", "count", "sum", "avg", "min", "max"]
MAX_FROZEN_COLUMNS = 3


# ─── Stored shapes ───────────────────────────────────────────────────────────


class _Strict(BaseModel):
    """Base for stored shapes: unknown keys are refused rather than kept."""

    model_config = ConfigDict(extra="forbid")


class ViewFilter(_Strict):
    id: Optional[str] = None
    schemaId: str
    operator: FilterOperator
    value: str = ""
    value2: Optional[str] = None
    dateMode: Optional[Literal["exact", "today", "relative"]] = None
    dateOffset: Optional[int] = None


class ViewFilterGroup(_Strict):
    id: Optional[str] = None
    conjunction: Literal["and", "or"] = "and"
    filters: list[ViewFilter] = []


class ViewSort(_Strict):
    id: Optional[str] = None
    schemaId: str
    direction: Literal["asc", "desc"] = "asc"


class ViewSettings(_Strict):
    """Presentation of a view. Every key is optional."""

    colOrder: Optional[list[str]] = None
    colWidths: Optional[dict[str, float]] = None
    hiddenColumns: Optional[list[str]] = None
    wrapColumns: Optional[list[str]] = None
    stickyHeader: Optional[bool] = None
    frozenColumns: Optional[int] = Field(default=None, ge=0, le=MAX_FROZEN_COLUMNS)
    groupBySchemaId: Optional[str] = None
    columnAggregations: Optional[dict[str, AggregationType]] = None
    calendarDateSchemaId: Optional[str] = None
    calendarSubtype: Optional[Literal["standard", "agenda"]] = None
    calendarGranularity: Optional[Literal["month", "week", "day"]] = None
    calendarChipColors: Optional[dict[str, str]] = None


def _validated_filter_groups(raw: Any) -> list[dict]:
    groups = [ViewFilterGroup.model_validate(g) for g in (raw or [])]
    return [g.model_dump(exclude_none=True) for g in groups]


def _validated_sorts(raw: Any) -> list[dict]:
    sorts = [ViewSort.model_validate(s) for s in (raw or [])]
    return [s.model_dump(exclude_none=True) for s in sorts]


def _validated_settings(raw: Any) -> dict:
    return ViewSettings.model_validate(raw or {}).model_dump(exclude_none=True)


def _unprocessable(exc: ValidationError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=[
            {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]}
            for err in exc.errors()
        ],
    )


# ─── Lookups ─────────────────────────────────────────────────────────────────


def get_database_or_404(db: Session, database_id: uuid.UUID) -> Block:
    block = repo.get_block(db, database_id)
    if block is None or block.type != "database":
        raise HTTPException(status_code=404, detail=f"Database {database_id} not found")
    return block


def require_readable(db: Session, database_id: uuid.UUID, user: User) -> Block:
    """The database, or 404 when it does not exist or *user* may not read it."""
    block = get_database_or_404(db, database_id)
    if not perm_repo.can_user_access(db, database_id, user):
        raise HTTPException(status_code=404, detail=f"Database {database_id} not found")
    return block


def require_writable(db: Session, database_id: uuid.UUID, user: User) -> Block:
    """The database, or 404 when it does not exist and 403 when *user* may not edit it."""
    block = get_database_or_404(db, database_id)
    if not perm_repo.can_user_access(db, database_id, user):
        raise HTTPException(status_code=403, detail="Not authorized for this block")
    return block


def get_view_or_404(
    db: Session, database_id: uuid.UUID, view_id: uuid.UUID, *, for_update: bool = False,
) -> DatabaseView:
    view = db.get(DatabaseView, view_id, with_for_update=for_update)
    if view is None or view.database_id != database_id:
        raise HTTPException(status_code=404, detail=f"View {view_id} not found")
    return view


def list_views(db: Session, database_id: uuid.UUID) -> list[DatabaseView]:
    stmt = (
        select(DatabaseView)
        .where(DatabaseView.database_id == database_id)
        .order_by(DatabaseView.position, DatabaseView.created_at)
    )
    return list(db.scalars(stmt))


def require_placement(db: Session, database_id: uuid.UUID, placement_id: uuid.UUID) -> None:
    """A placement is the database block itself or a linked block showing it."""
    if placement_id == database_id:
        return
    block = repo.get_block(db, placement_id)
    if block is None or block.type != "linked_database" or block.reference_id != database_id:
        raise HTTPException(
            status_code=422,
            detail=f"Block {placement_id} does not display database {database_id}",
        )


# ─── Bootstrap ───────────────────────────────────────────────────────────────


def _next_position(db: Session, database_id: uuid.UUID) -> float:
    current = db.scalar(
        select(func.max(DatabaseView.position)).where(DatabaseView.database_id == database_id)
    )
    return (current or 0.0) + 1.0


def default_settings(schemas: list[PropertySchema]) -> dict:
    """Settings of a new view: every column in order, system columns hidden."""
    return {
        "colOrder": [NAME_COL_KEY, *(str(s.id) for s in schemas)],
        "colWidths": {},
        "hiddenColumns": [str(s.id) for s in schemas if s.type in READONLY_SCHEMA_TYPES],
    }


def ensure_views(db: Session, database_id: uuid.UUID) -> list[DatabaseView]:
    """
    Return the database's views, creating what a displayed database needs.

    Missing system properties are created first, then a default view if the
    database has none, so the default view starts with those properties
    hidden. Flushes but does not commit.
    """
    repo.ensure_readonly_schemas(db, database_id)
    views = list_views(db, database_id)
    if views:
        return views
    schemas = repo.list_schemas(db, database_id)
    view = DatabaseView(
        database_id=database_id,
        name="",
        view_type="table",
        position=1.0,
        filter_groups=[],
        sorts=[],
        settings=default_settings(schemas),
    )
    db.add(view)
    db.flush()
    return [view]


def selected_view_id(
    db: Session,
    views: list[DatabaseView],
    placement_id: uuid.UUID,
) -> Optional[uuid.UUID]:
    """The view the placement shows: its stored selection, else the first view."""
    selection = db.get(DatabaseViewSelection, placement_id)
    ids = {v.id for v in views}
    if selection is not None and selection.view_id in ids:
        return selection.view_id
    return views[0].id if views else None


# ─── Mutations ───────────────────────────────────────────────────────────────


def create_view(
    db: Session,
    database_id: uuid.UUID,
    *,
    name: str = "",
    view_type: str = "table",
    description: Optional[str] = None,
    filter_groups: Any = None,
    sorts: Any = None,
    settings: Any = None,
) -> DatabaseView:
    """
    Append a view. Without explicit settings it gets the default layout.
    Raises 422 for an invalid shape.
    """
    if view_type not in VIEW_TYPES:
        raise HTTPException(status_code=422, detail=f"Unknown view type {view_type!r}")
    try:
        groups = _validated_filter_groups(filter_groups)
        sort_list = _validated_sorts(sorts)
        layout = (
            _validated_settings(settings)
            if settings is not None
            else default_settings(repo.list_schemas(db, database_id))
        )
    except ValidationError as exc:
        raise _unprocessable(exc) from exc
    view = DatabaseView(
        database_id=database_id,
        name=name,
        view_type=view_type,
        position=_next_position(db, database_id),
        description=_normalized_description(description),
        filter_groups=groups,
        sorts=sort_list,
        settings=layout,
    )
    db.add(view)
    db.flush()
    return view


def duplicate_view(
    db: Session, database_id: uuid.UUID, source: DatabaseView, *, name: str,
) -> DatabaseView:
    """Copy *source* and place the copy right after it."""
    following = db.scalar(
        select(func.min(DatabaseView.position)).where(
            DatabaseView.database_id == database_id,
            DatabaseView.position > source.position,
        )
    )
    position = (source.position + following) / 2.0 if following is not None else source.position + 1.0
    copy = DatabaseView(
        database_id=database_id,
        name=name,
        view_type=source.view_type,
        position=position,
        description=source.description,
        filter_groups=list(source.filter_groups or []),
        sorts=list(source.sorts or []),
        settings=dict(source.settings or {}),
    )
    db.add(copy)
    db.flush()
    return copy


def _normalized_description(raw: Optional[str]) -> Optional[str]:
    """Trim a description; keep inner line breaks; empty becomes None."""
    if raw is None:
        return None
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    return text or None


_UNSET: Any = object()


def update_view(
    db: Session,
    view: DatabaseView,
    *,
    name: Any = _UNSET,
    view_type: Any = _UNSET,
    description: Any = _UNSET,
    filter_groups: Any = _UNSET,
    sorts: Any = _UNSET,
    settings: Any = _UNSET,
) -> DatabaseView:
    """
    Apply the given fields to *view*. Fields left unset are not touched.

    *settings* is a partial mapping merged into the stored settings: each key
    given replaces the stored one, and a key given as ``None`` is removed.
    The merged result is validated as a whole. Raises 422 for an invalid
    shape and leaves the view unchanged.
    """
    try:
        if filter_groups is not _UNSET:
            filter_groups = _validated_filter_groups(filter_groups)
        if sorts is not _UNSET:
            sorts = _validated_sorts(sorts)
        if settings is not _UNSET:
            merged = dict(view.settings or {})
            for key, value in (settings or {}).items():
                if value is None:
                    merged.pop(key, None)
                else:
                    merged[key] = value
            settings = _validated_settings(merged)
    except ValidationError as exc:
        raise _unprocessable(exc) from exc

    if view_type is not _UNSET:
        if view_type not in VIEW_TYPES:
            raise HTTPException(status_code=422, detail=f"Unknown view type {view_type!r}")
        view.view_type = view_type
    if name is not _UNSET:
        view.name = name
    if description is not _UNSET:
        view.description = _normalized_description(description)
    if filter_groups is not _UNSET:
        view.filter_groups = filter_groups
    if sorts is not _UNSET:
        view.sorts = sorts
    if settings is not _UNSET:
        view.settings = settings
    db.flush()
    return view


def delete_view(db: Session, database_id: uuid.UUID, view: DatabaseView) -> None:
    """Delete *view*. The last view of a database cannot be deleted (409)."""
    remaining = db.scalar(
        select(func.count()).select_from(DatabaseView).where(
            DatabaseView.database_id == database_id
        )
    )
    if remaining <= 1:
        raise HTTPException(status_code=409, detail="A database keeps at least one view")
    db.delete(view)
    db.flush()


def reorder_views(db: Session, database_id: uuid.UUID, view_ids: list[uuid.UUID]) -> list[DatabaseView]:
    """
    Put the views in the order given. The list must name every view of the
    database exactly once (422 otherwise).
    """
    views = list_views(db, database_id)
    by_id = {v.id: v for v in views}
    if len(view_ids) != len(set(view_ids)) or set(view_ids) != set(by_id):
        raise HTTPException(
            status_code=422, detail="The order must list every view of the database once"
        )
    for index, view_id in enumerate(view_ids, start=1):
        by_id[view_id].position = float(index)
    db.flush()
    return [by_id[view_id] for view_id in view_ids]


def select_view(
    db: Session, database_id: uuid.UUID, placement_id: uuid.UUID, view_id: uuid.UUID,
) -> None:
    """Remember that *placement_id* shows *view_id*."""
    require_placement(db, database_id, placement_id)
    get_view_or_404(db, database_id, view_id)
    selection = db.get(DatabaseViewSelection, placement_id)
    if selection is None:
        db.add(DatabaseViewSelection(placement_id=placement_id, view_id=view_id))
    else:
        selection.view_id = view_id
    db.flush()


# ─── Property lifecycle ──────────────────────────────────────────────────────


def hide_schema_in_views(
    db: Session,
    database_id: uuid.UUID,
    schema_id: uuid.UUID,
    *,
    except_view_id: Optional[uuid.UUID] = None,
) -> None:
    """Add a new property to the hidden columns of every view but one."""
    key = str(schema_id)
    for view in list_views(db, database_id):
        if view.id == except_view_id:
            continue
        settings = dict(view.settings or {})
        hidden = list(settings.get("hiddenColumns") or [])
        if key in hidden:
            continue
        hidden.append(key)
        settings["hiddenColumns"] = hidden
        view.settings = settings
    db.flush()


def _forget_in_settings(settings: dict, key: str) -> dict:
    result = dict(settings)
    for list_key in ("colOrder", "hiddenColumns", "wrapColumns"):
        if list_key in result:
            result[list_key] = [k for k in result[list_key] if k != key]
    for map_key in ("colWidths", "columnAggregations"):
        if map_key in result:
            result[map_key] = {k: v for k, v in result[map_key].items() if k != key}
    for pointer_key in ("groupBySchemaId", "calendarDateSchemaId"):
        if result.get(pointer_key) == key:
            del result[pointer_key]
    return result


def forget_schema_in_views(db: Session, database_id: uuid.UUID, schema_id: uuid.UUID) -> None:
    """Remove every reference to a deleted property from the database's views."""
    key = str(schema_id)
    for view in list_views(db, database_id):
        groups = []
        for group in view.filter_groups or []:
            filters = [f for f in group.get("filters", []) if f.get("schemaId") != key]
            if filters:
                groups.append({**group, "filters": filters})
        view.filter_groups = groups
        view.sorts = [s for s in (view.sorts or []) if s.get("schemaId") != key]
        view.settings = _forget_in_settings(view.settings or {}, key)
    db.flush()


def forget_options_in_views(
    db: Session, database_id: uuid.UUID, schema_id: uuid.UUID, removed: set[str],
) -> None:
    """
    Remove filter conditions that test a deleted option of a property.

    A group left without conditions is dropped, as when a property is deleted.
    """
    if not removed:
        return
    key = str(schema_id)
    for view in list_views(db, database_id):
        groups, changed = options.strip_filter_groups(view.filter_groups or [], key, removed)
        if changed:
            view.filter_groups = groups
    db.flush()


# ─── Rendering ───────────────────────────────────────────────────────────────


def _query_descriptors(
    db: Session,
    schemas: list[PropertySchema],
    filter_groups: list[dict],
    sorts: list[dict],
) -> tuple[list[repo.FilterGroupDescriptor], list[repo.SortDescriptor]]:
    """
    Translate a view's stored filters and sorts into query descriptors.

    Conditions on properties that no longer exist are skipped, and a group
    left without conditions is dropped, as the query endpoint has always done.
    Formula conditions are compared by the result type the formula actually
    produces.
    """
    schema_map = {str(s.id): s for s in schemas}
    formula_ids = {
        uuid.UUID(f["schemaId"])
        for group in filter_groups
        for f in group.get("filters", [])
        if getattr(schema_map.get(f.get("schemaId")), "type", None) == "formula"
    }
    result_types = repo.infer_formula_result_types(db, list(formula_ids))

    groups: list[repo.FilterGroupDescriptor] = []
    for group in filter_groups:
        resolved = []
        for f in group.get("filters", []):
            schema = schema_map.get(f.get("schemaId"))
            result_type = (
                result_types.get(schema.id, "text")
                if schema is not None and schema.type == "formula"
                else None
            )
            descriptor = repo.resolve_filter_descriptor(
                schema_map,
                schema_id=f["schemaId"],
                operator=f["operator"],
                value=f.get("value", ""),
                date_mode=f.get("dateMode"),
                date_offset=f.get("dateOffset"),
                formula_result_type=result_type,
                value2=f.get("value2") or "",
            )
            if descriptor is not None:
                resolved.append(descriptor)
        if resolved:
            groups.append(repo.FilterGroupDescriptor(
                conjunction=group.get("conjunction", "and"), filters=resolved,
            ))

    sort_descriptors: list[repo.SortDescriptor] = []
    for s in sorts:
        if s["schemaId"] == NAME_COL_KEY:
            sort_descriptors.append(repo.SortDescriptor(
                schema_id=NAME_COL_KEY, schema_type=None, direction=s.get("direction", "asc"),
            ))
            continue
        schema = schema_map.get(s["schemaId"])
        if schema is None:
            continue
        sort_descriptors.append(repo.SortDescriptor(
            schema_id=s["schemaId"],
            schema_type=schema.type,
            schema_config=schema.config,
            direction=s.get("direction", "asc"),
        ))
    return groups, sort_descriptors


def entry_payloads(db: Session, entries: list[Block]) -> list[dict]:
    """Entries with all their property values, in the entry response shape."""
    values_map = repo.list_values_for_pages(db, [e.id for e in entries])
    return [
        {
            "id": entry.id,
            "position": entry.position,
            "content": entry.content,
            "icon": entry.icon,
            "state": entry.state,
            "values": {
                str(pv.property_schema_id): pv.value for pv in values_map.get(entry.id, [])
            },
        }
        for entry in entries
    ]


def render_page(
    db: Session,
    view: DatabaseView,
    user: User,
    *,
    offset: int = 0,
    limit: Optional[int] = None,
    search: str = "",
) -> dict:
    """
    One page of a view, ready to display.

    Filters and sorts come from the view. *search* narrows the result by
    entry name in addition, without being stored. Views laid out on a time
    axis are never paged: *offset* and *limit* are ignored for them and every
    entry up to ``MAX_PAGE_SIZE`` is returned.

    The page carries the resolution of every relation its entries hold, so
    nothing has to be asked for separately.
    """
    schemas = repo.list_schemas(db, view.database_id)
    groups, sorts = _query_descriptors(db, schemas, view.filter_groups or [], view.sorts or [])

    query = search.strip()
    if query:
        name_filter = repo.resolve_filter_descriptor(
            {}, schema_id=NAME_COL_KEY, operator="contains", value=query,
        )
        groups.append(repo.FilterGroupDescriptor(conjunction="and", filters=[name_filter]))

    if view.view_type in UNPAGED_VIEW_TYPES or limit is None:
        offset, limit = 0, MAX_PAGE_SIZE
    else:
        offset = max(0, offset)
        limit = min(max(1, limit), MAX_PAGE_SIZE)

    entries, total = repo.query_entries(db, view.database_id, groups, sorts, limit, offset)
    payloads = entry_payloads(db, entries)
    resolved = resolution.resolve_relations(db, schemas, (p["values"] for p in payloads), user)
    return {
        "entries": payloads,
        "total": total,
        "offset": offset,
        **resolved.as_payload(),
    }
