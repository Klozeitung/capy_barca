"""migrate database views from block preferences into database_views

Revision ID: s9v1u0q20019
Revises: r8u0t9p10018
Create Date: 2026-10-17

Until now a database's views lived in the generic block preference store of
the database block:

* ``views``        – JSON array of view objects, written by the frontend
* ``active_view``  – id of the view the database showed
* ``col_widths``   – column widths from before views existed, read only when
                     ``views`` was missing

This revision moves them into ``database_views`` and
``database_view_selections`` and then deletes the three preference keys of
every database block. Nothing else in block_preferences is touched.

Conversion
----------
The preference store never validated anything, so the stored views are read
leniently and written in the strict shape the backend validates on every
later write (``app.blocks.views``):

* Legacy forms the frontend used to convert on load are converted here: a flat
  ``filters`` list becomes one AND group, a missing view type becomes
  ``table``.
* Values that have an unambiguous strict form are converted: a filter value
  stored as a number or boolean becomes its string, a whole-number float
  offset becomes an int, a frozen-column count outside the allowed range is
  clamped.
* Parts that cannot be used are dropped, and every dropped part is logged with
  its database and view, so the output of the upgrade lists exactly what was
  removed. A view is never dropped as a whole; an unusable view entry (not an
  object) is.

Selections
----------
Before this revision every placement of a database showed the view stored in
the database's ``active_view``: the database block itself and every
``linked_database`` block pointing at it. Each of them receives that view as
its own selection, so the update changes nothing visible. From then on each
placement keeps its own choice.

Existing rows
-------------
A database whose ``views`` preference is migrated loses any rows it already
has in ``database_views``. Such rows can only be the default view the
backend creates on first request, which no released frontend has asked for.

Downgrade
---------
Not supported. This is a data migration; the way back is restoring a backup.
"""
import json
import logging
import uuid
from typing import Any, Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "s9v1u0q20019"
down_revision: Union[str, None] = "r8u0t9p10018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

PREF_VIEWS = "views"
PREF_ACTIVE_VIEW = "active_view"
PREF_COL_WIDTHS = "col_widths"

NAME_COL_KEY = "__name__"

# Mirrors app.blocks.views. Copied rather than imported: a migration must keep
# producing the same result when the application code changes later.
VIEW_TYPES = ("table", "calendar", "list", "gallery", "board", "family_tree", "mindmap", "graph")
READONLY_SCHEMA_TYPES = frozenset({
    "id", "created_by", "created_time", "last_edited_by", "last_edited_time",
    "parent_item", "sub_item",
})
FILTER_OPERATORS = frozenset({
    "eq", "neq",
    "contains", "not_contains", "starts_with", "ends_with",
    "is_empty", "is_not_empty",
    "gt", "gte", "lt", "lte",
    "past_week", "past_month", "past_year",
    "this_week", "next_week", "next_month", "next_year",
    "between",
})
DATE_MODES = frozenset({"exact", "today", "relative"})
AGGREGATIONS = frozenset({"none", "count", "sum", "avg", "min", "max"})
CALENDAR_SUBTYPES = frozenset({"standard", "agenda"})
CALENDAR_GRANULARITIES = frozenset({"month", "week", "day"})
MAX_FROZEN_COLUMNS = 3


# ─── Conversion (pure, no database access) ────────────────────────────────────


class Report:
    """Collects what the conversion dropped, for the upgrade's output."""

    def __init__(self) -> None:
        self.dropped: list[str] = []

    def drop(self, where: str, what: str) -> None:
        self.dropped.append(f"{where}: {what}")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _as_text(value: Any) -> Optional[str]:
    """A scalar as the string the frontend would have sent, or None."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if _is_number(value):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return None


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _string_list(raw: Any, where: str, key: str, report: Report) -> Optional[list[str]]:
    if raw is None:
        return None
    if not isinstance(raw, list):
        report.drop(where, f"{key} (not a list)")
        return None
    result = []
    for item in raw:
        if isinstance(item, str):
            result.append(item)
        else:
            report.drop(where, f"{key} item {item!r}")
    return result


def _convert_filter(raw: Any, where: str, report: Report) -> Optional[dict]:
    if not isinstance(raw, dict):
        report.drop(where, f"filter {raw!r} (not an object)")
        return None
    schema_id = raw.get("schemaId")
    operator = raw.get("operator")
    if not isinstance(schema_id, str) or not schema_id:
        report.drop(where, f"filter {raw!r} (no property)")
        return None
    if operator not in FILTER_OPERATORS:
        report.drop(where, f"filter {raw!r} (unknown operator)")
        return None

    result: dict[str, Any] = {"schemaId": schema_id, "operator": operator}
    if isinstance(raw.get("id"), str):
        result["id"] = raw["id"]

    value = raw.get("value")
    text = "" if value is None else _as_text(value)
    if text is None:
        report.drop(where, f"filter value {value!r}")
        text = ""
    result["value"] = text

    if raw.get("value2") is not None:
        text2 = _as_text(raw["value2"])
        if text2 is None:
            report.drop(where, f"filter value2 {raw['value2']!r}")
        else:
            result["value2"] = text2

    if raw.get("dateMode") is not None:
        if raw["dateMode"] in DATE_MODES:
            result["dateMode"] = raw["dateMode"]
        else:
            report.drop(where, f"filter dateMode {raw['dateMode']!r}")

    if raw.get("dateOffset") is not None:
        offset = _as_int(raw["dateOffset"])
        if offset is None:
            report.drop(where, f"filter dateOffset {raw['dateOffset']!r}")
        else:
            result["dateOffset"] = offset

    for key in raw:
        if key not in ("id", "schemaId", "operator", "value", "value2", "dateMode", "dateOffset"):
            report.drop(where, f"filter key {key!r}")
    return result


def _convert_group(raw: Any, where: str, report: Report) -> Optional[dict]:
    if not isinstance(raw, dict):
        report.drop(where, f"filter group {raw!r} (not an object)")
        return None
    result: dict[str, Any] = {}
    if isinstance(raw.get("id"), str):
        result["id"] = raw["id"]
    conjunction = raw.get("conjunction", "and")
    if conjunction not in ("and", "or"):
        report.drop(where, f"filter group conjunction {conjunction!r}, using 'and'")
        conjunction = "and"
    result["conjunction"] = conjunction
    raw_filters = raw.get("filters") or []
    if not isinstance(raw_filters, list):
        report.drop(where, "filter group filters (not a list)")
        raw_filters = []
    result["filters"] = [
        f for f in (_convert_filter(item, where, report) for item in raw_filters) if f is not None
    ]
    for key in raw:
        if key not in ("id", "conjunction", "filters"):
            report.drop(where, f"filter group key {key!r}")
    return result


def _convert_filter_groups(view: dict, where: str, report: Report) -> list[dict]:
    raw_groups = view.get("filterGroups")
    if raw_groups is None:
        raw_groups = []
    if not isinstance(raw_groups, list):
        report.drop(where, "filterGroups (not a list)")
        raw_groups = []
    groups = [g for g in (_convert_group(item, where, report) for item in raw_groups) if g is not None]

    # Legacy flat list: one more AND group, as the frontend converted it on load.
    legacy = view.get("filters")
    if isinstance(legacy, list) and legacy:
        converted = _convert_group({"conjunction": "and", "filters": legacy}, where, report)
        if converted is not None and converted["filters"]:
            converted["id"] = str(uuid.uuid4())
            groups.append(converted)
    elif legacy not in (None, []):
        report.drop(where, "filters (not a list)")
    return groups


def _convert_sorts(view: dict, where: str, report: Report) -> list[dict]:
    raw_sorts = view.get("sorts")
    if raw_sorts is None:
        return []
    if not isinstance(raw_sorts, list):
        report.drop(where, "sorts (not a list)")
        return []
    sorts = []
    for raw in raw_sorts:
        if not isinstance(raw, dict) or not isinstance(raw.get("schemaId"), str) or not raw["schemaId"]:
            report.drop(where, f"sort {raw!r}")
            continue
        sort: dict[str, Any] = {"schemaId": raw["schemaId"]}
        if isinstance(raw.get("id"), str):
            sort["id"] = raw["id"]
        direction = raw.get("direction", "asc")
        if direction not in ("asc", "desc"):
            report.drop(where, f"sort direction {direction!r}, using 'asc'")
            direction = "asc"
        sort["direction"] = direction
        for key in raw:
            if key not in ("id", "schemaId", "direction"):
                report.drop(where, f"sort key {key!r}")
        sorts.append(sort)
    return sorts


def _number_map(raw: Any, where: str, key: str, report: Report) -> Optional[dict[str, float]]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        report.drop(where, f"{key} (not an object)")
        return None
    result = {}
    for k, v in raw.items():
        if _is_number(v):
            result[k] = v
        elif isinstance(v, str):
            try:
                result[k] = float(v)
            except ValueError:
                report.drop(where, f"{key}[{k!r}] {v!r}")
        else:
            report.drop(where, f"{key}[{k!r}] {v!r}")
    return result


def _choice_map(
    raw: Any, allowed: Optional[frozenset[str]], where: str, key: str, report: Report,
) -> Optional[dict[str, str]]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        report.drop(where, f"{key} (not an object)")
        return None
    result = {}
    for k, v in raw.items():
        if isinstance(v, str) and (allowed is None or v in allowed):
            result[k] = v
        else:
            report.drop(where, f"{key}[{k!r}] {v!r}")
    return result


def _convert_settings(view: dict, where: str, report: Report) -> dict:
    settings: dict[str, Any] = {}

    for key in ("colOrder", "hiddenColumns", "wrapColumns"):
        value = _string_list(view.get(key), where, key, report)
        if value is not None:
            settings[key] = value

    widths = _number_map(view.get("colWidths"), where, "colWidths", report)
    if widths is not None:
        settings["colWidths"] = widths

    if view.get("stickyHeader") is not None:
        if isinstance(view["stickyHeader"], bool):
            settings["stickyHeader"] = view["stickyHeader"]
        else:
            report.drop(where, f"stickyHeader {view['stickyHeader']!r}")

    if view.get("frozenColumns") is not None:
        frozen = _as_int(view["frozenColumns"])
        if frozen is None:
            report.drop(where, f"frozenColumns {view['frozenColumns']!r}")
        else:
            settings["frozenColumns"] = max(0, min(MAX_FROZEN_COLUMNS, frozen))

    for key in ("groupBySchemaId", "calendarDateSchemaId"):
        value = view.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, str):
            settings[key] = value
        else:
            report.drop(where, f"{key} {value!r}")

    aggregations = _choice_map(
        view.get("columnAggregations"), AGGREGATIONS, where, "columnAggregations", report,
    )
    if aggregations is not None:
        settings["columnAggregations"] = aggregations

    for key, allowed in (
        ("calendarSubtype", CALENDAR_SUBTYPES),
        ("calendarGranularity", CALENDAR_GRANULARITIES),
    ):
        value = view.get(key)
        if value is None:
            continue
        if value in allowed:
            settings[key] = value
        else:
            report.drop(where, f"{key} {value!r}")

    colors = _choice_map(view.get("calendarChipColors"), None, where, "calendarChipColors", report)
    if colors is not None:
        settings["calendarChipColors"] = colors

    return settings


# Keys of a stored view that are carried over above, or deliberately ignored.
_KNOWN_VIEW_KEYS = frozenset({
    "id", "name", "viewType", "description", "filterGroups", "filters", "sorts",
    "colOrder", "colWidths", "hiddenColumns", "wrapColumns", "stickyHeader",
    "frozenColumns", "groupBySchemaId", "calendarDateSchemaId", "columnAggregations",
    "calendarSubtype", "calendarGranularity", "calendarChipColors",
})


def _normalized_description(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    return text or None


def default_settings(schemas: list[tuple[str, str]], col_widths: dict[str, float]) -> dict:
    """Layout of a new default view, as the frontend built it."""
    return {
        "colOrder": [NAME_COL_KEY, *(schema_id for schema_id, _ in schemas)],
        "colWidths": col_widths,
        "hiddenColumns": [schema_id for schema_id, type_ in schemas if type_ in READONLY_SCHEMA_TYPES],
    }


def convert_views(
    database_id: str,
    raw_views: Any,
    raw_active: Any,
    raw_col_widths: Any,
    schemas: list[tuple[str, str]],
    report: Report,
) -> tuple[list[dict], Optional[str]]:
    """
    Convert the stored preferences of one database into view rows.

    *schemas* lists ``(id, type)`` of the database's properties in position
    order; it is only used when there are no stored views but legacy column
    widths, which the frontend turned into a default view.

    Returns the rows (``id``, ``name``, ``view_type``, ``position``,
    ``description``, ``filter_groups``, ``sorts``, ``settings``) and the id of
    the selected view, or None when no stored selection points at a migrated
    view.
    """
    rows: list[dict] = []
    id_map: dict[str, str] = {}

    if isinstance(raw_views, list) and raw_views:
        for index, raw in enumerate(raw_views):
            where = f"database {database_id}, view #{index + 1}"
            if not isinstance(raw, dict):
                report.drop(where, "entry (not an object)")
                continue

            old_id = raw.get("id")
            new_id: Optional[str] = None
            if isinstance(old_id, str):
                try:
                    candidate = str(uuid.UUID(old_id))
                except ValueError:
                    candidate = None
                if candidate is not None and candidate not in {r["id"] for r in rows}:
                    new_id = candidate
            if new_id is None:
                new_id = str(uuid.uuid4())
            if isinstance(old_id, str) and old_id not in id_map:
                id_map[old_id] = new_id
            where = f"database {database_id}, view {new_id}"

            view_type = raw.get("viewType") or "table"
            if view_type not in VIEW_TYPES:
                report.drop(where, f"viewType {view_type!r}, using 'table'")
                view_type = "table"

            name = raw.get("name")
            if name is None:
                name = ""
            elif not isinstance(name, str):
                name = _as_text(name) or ""

            for key in raw:
                if key not in _KNOWN_VIEW_KEYS:
                    report.drop(where, f"key {key!r}")

            rows.append({
                "id": new_id,
                "name": name,
                "view_type": view_type,
                "position": float(len(rows) + 1),
                "description": _normalized_description(raw.get("description")),
                "filter_groups": _convert_filter_groups(raw, where, report),
                "sorts": _convert_sorts(raw, where, report),
                "settings": _convert_settings(raw, where, report),
            })
    elif isinstance(raw_col_widths, dict) and raw_col_widths:
        where = f"database {database_id}, legacy column widths"
        widths = _number_map(raw_col_widths, where, "col_widths", report) or {}
        rows.append({
            "id": str(uuid.uuid4()),
            "name": "",
            "view_type": "table",
            "position": 1.0,
            "description": None,
            "filter_groups": [],
            "sorts": [],
            "settings": default_settings(schemas, widths),
        })

    selected = id_map.get(raw_active) if isinstance(raw_active, str) else None
    return rows, selected


# ─── Database access ──────────────────────────────────────────────────────────


def _load_json(value: Any) -> Any:
    """json columns come back decoded from psycopg; tolerate raw strings too."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def migrate(connection: sa.engine.Connection) -> Report:
    """Move every database's view preferences into the view tables."""
    report = Report()
    prefs = connection.execute(sa.text(
        """
        SELECT p.block_id, p.key, p.value
        FROM block_preferences p
        JOIN blocks b ON b.id = p.block_id
        WHERE b.type = 'database' AND p.key IN (:views, :active, :widths)
        """
    ), {"views": PREF_VIEWS, "active": PREF_ACTIVE_VIEW, "widths": PREF_COL_WIDTHS}).all()

    by_database: dict[uuid.UUID, dict[str, Any]] = {}
    for block_id, key, value in prefs:
        by_database.setdefault(block_id, {})[key] = _load_json(value)

    insert_view = sa.text(
        """
        INSERT INTO database_views
            (id, database_id, name, view_type, position, description,
             filter_groups, sorts, settings)
        VALUES
            (:id, :database_id, :name, :view_type, :position, :description,
             CAST(:filter_groups AS jsonb), CAST(:sorts AS jsonb), CAST(:settings AS jsonb))
        """
    )
    insert_selection = sa.text(
        "INSERT INTO database_view_selections (placement_id, view_id) VALUES (:placement, :view)"
    )

    for database_id, stored in by_database.items():
        schemas = [
            (str(schema_id), type_)
            for schema_id, type_ in connection.execute(sa.text(
                "SELECT id, type FROM property_schemas WHERE database_id = :db ORDER BY position"
            ), {"db": database_id})
        ]
        rows, selected = convert_views(
            str(database_id),
            stored.get(PREF_VIEWS),
            stored.get(PREF_ACTIVE_VIEW),
            stored.get(PREF_COL_WIDTHS),
            schemas,
            report,
        )
        if not rows:
            continue

        connection.execute(
            sa.text("DELETE FROM database_views WHERE database_id = :db"), {"db": database_id},
        )
        for row in rows:
            connection.execute(insert_view, {
                **row,
                "database_id": database_id,
                "filter_groups": json.dumps(row["filter_groups"]),
                "sorts": json.dumps(row["sorts"]),
                "settings": json.dumps(row["settings"]),
            })

        if selected is not None:
            placements = [database_id, *connection.scalars(sa.text(
                "SELECT id FROM blocks WHERE type = 'linked_database' AND reference_id = :db"
            ), {"db": database_id})]
            for placement in placements:
                connection.execute(insert_selection, {"placement": placement, "view": selected})

    connection.execute(sa.text(
        """
        DELETE FROM block_preferences p
        USING blocks b
        WHERE b.id = p.block_id AND b.type = 'database' AND p.key IN (:views, :active, :widths)
        """
    ), {"views": PREF_VIEWS, "active": PREF_ACTIVE_VIEW, "widths": PREF_COL_WIDTHS})

    return report


def upgrade() -> None:
    report = migrate(op.get_bind())
    for line in report.dropped:
        log.warning("view migration dropped %s", line)
    if report.dropped:
        log.warning("view migration dropped %d part(s) in total", len(report.dropped))


def downgrade() -> None:
    raise NotImplementedError(
        "Revision s9v1u0q20019 migrates data one way; restore a backup to go back."
    )
