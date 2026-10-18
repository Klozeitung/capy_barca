"""
Tests for revision 0019, which moves database views out of block preferences.

The conversion is tested as a pure function; ``migrate`` is run against the
PostgreSQL test database, whose schema the migrations have already built, so
it operates on exactly the tables it will meet in production.
"""
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.blocks import views as views_module

_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0019_migrate_view_preferences.py"
_spec = importlib.util.spec_from_file_location("migration_0019", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


def _convert(raw_views, raw_active=None, raw_col_widths=None, schemas=()):
    report = migration.Report()
    rows, selected = migration.convert_views(
        "db", raw_views, raw_active, raw_col_widths, list(schemas), report,
    )
    return rows, selected, report


def _view(**fields):
    base = {
        "id": str(uuid.uuid4()),
        "name": "View A",
        "viewType": "table",
        "colOrder": ["__name__"],
        "colWidths": {},
        "filterGroups": [],
        "sorts": [],
        "hiddenColumns": [],
    }
    base.update(fields)
    return base


def _assert_valid(row):
    """The row passes the validation every later write goes through."""
    for group in row["filter_groups"]:
        views_module.ViewFilterGroup.model_validate(group)
    for sort in row["sorts"]:
        views_module.ViewSort.model_validate(sort)
    views_module.ViewSettings.model_validate(row["settings"])
    assert row["view_type"] in views_module.VIEW_TYPES


# ─── Conversion ──────────────────────────────────────────────────────────────


def test_a_current_view_is_carried_over_unchanged():
    view = _view(
        description="Line 1\nLine 2",
        filterGroups=[{"id": "g1", "conjunction": "or", "filters": [
            {"id": "f1", "schemaId": "s1", "operator": "eq", "value": "x"},
            {"id": "f2", "schemaId": "s2", "operator": "gt", "value": "2000-07-28",
             "dateMode": "relative", "dateOffset": -3},
        ]}],
        sorts=[{"id": "o1", "schemaId": "__name__", "direction": "desc"}],
        colWidths={"__name__": 240},
        wrapColumns=["s1"],
        stickyHeader=False,
        frozenColumns=2,
        groupBySchemaId="s1",
        columnAggregations={"s2": "sum"},
        calendarDateSchemaId="s2",
        calendarSubtype="agenda",
        calendarGranularity="week",
        calendarChipColors={"e1": "red"},
    )

    [row], _, report = _convert([view])

    assert report.dropped == []
    assert row["id"] == view["id"]
    assert row["name"] == "View A"
    assert row["description"] == "Line 1\nLine 2"
    assert row["filter_groups"] == view["filterGroups"]
    assert row["sorts"] == view["sorts"]
    assert row["settings"] == {
        "colOrder": ["__name__"],
        "colWidths": {"__name__": 240},
        "hiddenColumns": [],
        "wrapColumns": ["s1"],
        "stickyHeader": False,
        "frozenColumns": 2,
        "groupBySchemaId": "s1",
        "columnAggregations": {"s2": "sum"},
        "calendarDateSchemaId": "s2",
        "calendarSubtype": "agenda",
        "calendarGranularity": "week",
        "calendarChipColors": {"e1": "red"},
    }
    _assert_valid(row)


def test_legacy_flat_filters_become_an_and_group():
    legacy = [{"id": "f1", "schemaId": "s1", "operator": "contains", "value": "x"}]
    view = _view(filters=legacy)
    del view["filterGroups"]

    [row], _, report = _convert([view])

    assert report.dropped == []
    [group] = row["filter_groups"]
    assert group["conjunction"] == "and"
    assert group["filters"] == legacy
    _assert_valid(row)


def test_an_already_converted_legacy_list_is_ignored():
    [row], _, report = _convert([_view(filters=[])])
    assert row["filter_groups"] == []
    assert report.dropped == []


def test_missing_view_type_becomes_table():
    view = _view()
    del view["viewType"]
    [row], _, _ = _convert([view])
    assert row["view_type"] == "table"


def test_scalar_filter_values_become_strings():
    view = _view(filterGroups=[{"conjunction": "and", "filters": [
        {"schemaId": "s1", "operator": "eq", "value": 5},
        {"schemaId": "s2", "operator": "eq", "value": True},
        {"schemaId": "s3", "operator": "gt", "value": 2.5},
        {"schemaId": "s4", "operator": "is_empty", "value": None},
        {"schemaId": "s5", "operator": "eq", "value": "x", "dateOffset": 4.0},
    ]}])

    [row], _, report = _convert([view])

    values = [f["value"] for f in row["filter_groups"][0]["filters"]]
    assert values == ["5", "true", "2.5", "", "x"]
    assert row["filter_groups"][0]["filters"][4]["dateOffset"] == 4
    assert report.dropped == []
    _assert_valid(row)


def test_unusable_parts_are_dropped_and_reported():
    view = _view(
        filterGroups=[{"conjunction": "xor", "filters": [
            {"schemaId": "s1", "operator": "resembles", "value": "x"},
            {"operator": "eq", "value": "x"},
            {"schemaId": "s2", "operator": "eq", "value": {"nested": 1}},
        ]}],
        sorts=[{"schemaId": "s1", "direction": "sideways"}, "s2"],
        colWidths={"s1": "wide", "s2": 80},
        columnAggregations={"s1": "median"},
        calendarSubtype="year",
        unknownKey=1,
    )

    [row], _, report = _convert([view])

    [group] = row["filter_groups"]
    assert group["conjunction"] == "and"
    assert group["filters"] == [{"schemaId": "s2", "operator": "eq", "value": ""}]
    assert row["sorts"] == [{"schemaId": "s1", "direction": "asc"}]
    assert row["settings"]["colWidths"] == {"s2": 80}
    assert row["settings"]["columnAggregations"] == {}
    assert "calendarSubtype" not in row["settings"]
    dropped = "\n".join(report.dropped)
    for fragment in ("conjunction", "resembles", "no property", "nested", "sideways",
                     "'s2'", "wide", "median", "year", "unknownKey"):
        assert fragment in dropped
    _assert_valid(row)


def test_frozen_columns_are_clamped():
    [row], _, _ = _convert([_view(frozenColumns=7)])
    assert row["settings"]["frozenColumns"] == views_module.MAX_FROZEN_COLUMNS


def test_empty_group_by_means_no_grouping():
    [row], _, report = _convert([_view(groupBySchemaId="")])
    assert "groupBySchemaId" not in row["settings"]
    assert report.dropped == []


def test_invalid_and_repeated_ids_are_replaced_and_selection_follows():
    first = _view(id="not-a-uuid")
    second = _view(id=first["id"])
    shared = str(uuid.uuid4())
    third = _view(id=shared)
    fourth = _view(id=shared)

    rows, selected, _ = _convert([first, second, third, fourth], raw_active="not-a-uuid")

    ids = [r["id"] for r in rows]
    assert len(set(ids)) == 4
    assert ids[2] == shared
    assert selected == ids[0]
    assert [r["position"] for r in rows] == [1.0, 2.0, 3.0, 4.0]


def test_selection_of_an_unknown_view_is_dropped():
    _, selected, _ = _convert([_view()], raw_active=str(uuid.uuid4()))
    assert selected is None


def test_entries_that_are_not_views_are_dropped():
    rows, _, report = _convert(["text", _view()])
    assert len(rows) == 1
    assert "not an object" in report.dropped[0]


def test_legacy_column_widths_become_a_default_view():
    schemas = [("s1", "text"), ("s2", "created_time")]

    [row], selected, _ = _convert(None, raw_col_widths={"s1": 150}, schemas=schemas)

    assert row["name"] == ""
    assert row["settings"] == {
        "colOrder": ["__name__", "s1", "s2"],
        "colWidths": {"s1": 150},
        "hiddenColumns": ["s2"],
    }
    assert selected is None


def test_nothing_stored_yields_nothing():
    rows, selected, _ = _convert(None)
    assert rows == []
    assert selected is None


# ─── Against the database ────────────────────────────────────────────────────


def _block(connection, type_, reference_id=None):
    block_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position, state, reference_id) "
        "VALUES (:id, :type, 1.0, 'active', :ref)"
    ), {"id": block_id, "type": type_, "ref": reference_id})
    return block_id


def _preference(connection, block_id, key, value):
    connection.execute(sa.text(
        "INSERT INTO block_preferences (id, block_id, key, value) "
        "VALUES (:id, :block, :key, CAST(:value AS json))"
    ), {"id": uuid.uuid4(), "block": block_id, "key": key, "value": json.dumps(value)})


@pytest.fixture
def connection(test_engine):
    with test_engine.begin() as conn:
        yield conn


def test_migrate_moves_views_and_gives_every_placement_the_active_view(connection):
    database = _block(connection, "database")
    linked_a = _block(connection, "linked_database", database)
    linked_b = _block(connection, "linked_database", database)
    first, second = _view(name="View A"), _view(name="View B")
    _preference(connection, database, "views", [first, second])
    _preference(connection, database, "active_view", second["id"])
    _preference(connection, database, "folded", True)
    # A default view the backend may have created before the migration.
    connection.execute(sa.text(
        "INSERT INTO database_views (id, database_id, position) VALUES (:id, :db, 1.0)"
    ), {"id": uuid.uuid4(), "db": database})

    report = migration.migrate(connection)

    rows = connection.execute(sa.text(
        "SELECT id, name, settings FROM database_views WHERE database_id = :db ORDER BY position"
    ), {"db": database}).all()
    assert [(str(r.id), r.name) for r in rows] == [(first["id"], "View A"), (second["id"], "View B")]
    assert rows[0].settings["colOrder"] == ["__name__"]
    selections = dict(connection.execute(sa.text(
        "SELECT placement_id, view_id FROM database_view_selections"
    )).all())
    assert {k: str(v) for k, v in selections.items()} == {
        database: second["id"], linked_a: second["id"], linked_b: second["id"],
    }
    keys = set(connection.scalars(sa.text(
        "SELECT key FROM block_preferences WHERE block_id = :db"
    ), {"db": database}))
    assert keys == {"folded"}
    assert report.dropped == []


def test_migrate_leaves_databases_without_stored_views_alone(connection):
    database = _block(connection, "database")
    _preference(connection, database, "folded", False)

    migration.migrate(connection)

    count = connection.scalar(sa.text(
        "SELECT count(*) FROM database_views WHERE database_id = :db"
    ), {"db": database})
    assert count == 0


def test_migrate_ignores_preferences_of_other_blocks(connection):
    page = _block(connection, "page")
    _preference(connection, page, "views", [_view()])

    migration.migrate(connection)

    assert connection.scalar(sa.text("SELECT count(*) FROM database_views")) == 0
    assert connection.scalar(sa.text(
        "SELECT count(*) FROM block_preferences WHERE block_id = :p"
    ), {"p": page}) == 1


def test_downgrade_is_refused():
    with pytest.raises(NotImplementedError):
        migration.downgrade()
