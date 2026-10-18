"""
Tests for revision 0020, which gives choice options and nuances stable ids.

The conversion is tested as pure functions; ``migrate`` runs against the
PostgreSQL test database, whose schema the migrations have already built.
"""
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0020_option_ids.py"
_spec = importlib.util.spec_from_file_location("migration_0020", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

IDS = {"Value A": "id-a", "Value B": "id-b", "id-a": "id-a", "id-b": "id-b"}


# ─── Options ─────────────────────────────────────────────────────────────────


def test_assign_ids_turns_strings_into_options_and_keeps_colours():
    options = migration.assign_ids(["Value A", {"label": " Value B ", "color": "red"}, "", "Value A"])
    assert [o["label"] for o in options] == ["Value A", "Value B"]
    assert options[1]["color"] == "red"
    assert all(uuid.UUID(o["id"]) for o in options)


def test_assign_ids_reuses_known_ids_and_keeps_existing_ones():
    options = migration.assign_ids(
        [{"label": "Value A"}, {"id": "keep", "label": "Value B"}],
        known={"Value A": "shared"},
    )
    assert [o["id"] for o in options] == ["shared", "keep"]


# ─── Values ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("before, after", [
    ({"option": "Value A"}, {"option": "id-a"}),
    ({"options": ["Value A", "Value B"]}, {"options": ["id-a", "id-b"]}),
    ({"selected": "Value B"}, {"option": "id-b"}),
    ({"selected": ["Value B"]}, {"options": ["id-b"]}),
    ({"option": "id-a"}, {"option": "id-a"}),
    ({"option": ""}, None),
    ({"option": "Gone"}, None),
    ({"options": ["Gone", "Value A"]}, {"options": ["id-a"]}),
    ({"options": ["Gone"]}, None),
])
def test_select_values_hold_ids(before, after):
    assert migration.convert_select_value(before, IDS, set()) == after


def test_select_timeline_slots_hold_ids_and_empty_slots_go():
    value = {"_timeline": {
        "": {"option": "Value A"},
        "2000-07-28→": {"option": "Gone"},
    }}
    unknown: set[str] = set()
    assert migration.convert_select_value(value, IDS, unknown) == {
        "_timeline": {"": {"option": "id-a"}},
    }
    assert unknown == {"Gone"}


def test_nuances_hold_ids_in_every_place():
    value = {
        "related_ids": ["u1", "u2"],
        "nuances": {"u1": "Value A", "u2": "Gone"},
        "nuancePool": {"u1": {"": "Value B"}, "u2": {"": "Gone"}},
        "_timeline": {"": {"related_ids": ["u1"], "nuances": {"u1": "Value A"}}},
    }
    unknown: set[str] = set()
    assert migration.convert_nuance_value(value, IDS, unknown) == {
        "related_ids": ["u1", "u2"],
        "nuances": {"u1": "id-a"},
        "nuancePool": {"u1": {"": "id-b"}},
        "_timeline": {"": {"related_ids": ["u1"], "nuances": {"u1": "id-a"}}},
    }
    assert unknown == {"Gone"}


def test_empty_nuance_maps_are_removed():
    value = {"related_ids": ["u1"], "nuances": {"u1": "Gone"}}
    assert migration.convert_nuance_value(value, IDS, set()) == {"related_ids": ["u1"]}


# ─── Filters and automations ─────────────────────────────────────────────────


def test_filter_conditions_on_select_compare_ids():
    report = migration.Report()
    groups = [
        {"conjunction": "or", "filters": [
            {"schemaId": "s1", "operator": "eq", "value": "Value A"},
            {"schemaId": "s1", "operator": "eq", "value": "Gone"},
            {"schemaId": "other", "operator": "eq", "value": "Value A"},
        ]},
        {"conjunction": "and", "filters": [
            {"schemaId": "s1", "operator": "neq", "value": "Gone"},
        ]},
        {"conjunction": "and", "filters": [
            {"schemaId": "s1", "operator": "is_empty", "value": ""},
        ]},
    ]

    result, changed = migration.convert_filter_groups(groups, {"s1": IDS}, "view v", report)

    assert changed
    assert result == [
        {"conjunction": "or", "filters": [
            {"schemaId": "s1", "operator": "eq", "value": "id-a"},
            {"schemaId": "other", "operator": "eq", "value": "Value A"},
        ]},
        {"conjunction": "and", "filters": [
            {"schemaId": "s1", "operator": "is_empty", "value": ""},
        ]},
    ]
    assert len(report.dropped) == 2


def test_automation_actions_write_and_test_ids():
    schema_id = str(uuid.uuid4())
    actions = [
        {
            "endpoint": f"PUT /api/databases/{{trigger.db_uuid}}/entries/{{trigger.entry_id}}/values/{schema_id}",
            "body": {"value": {"option": "Value B"}},
        },
        {
            "endpoint": f"PUT /api/databases/db/bulk-values/{schema_id}",
            "body": {"value": {"option": "Gone"}},
            "filter": {"mode": "where", "groups": [
                {"conjunction": "and", "filters": [
                    {"schemaId": schema_id, "operator": "eq", "value": "Value A"},
                ]},
            ]},
        },
    ]
    report = migration.Report()

    result, changed = migration.convert_actions(actions, {schema_id: IDS}, "automation a", report)

    assert changed
    assert result[0]["body"] == {"value": {"option": "id-b"}}
    assert result[1]["body"] == {"value": None}
    assert result[1]["filter"]["groups"][0]["filters"][0]["value"] == "id-a"
    assert len(report.dropped) == 1


# ─── Against the database ────────────────────────────────────────────────────


@pytest.fixture
def connection(test_engine):
    with test_engine.begin() as conn:
        yield conn


def _block(connection, type_):
    block_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position, state) VALUES (:id, :type, 1.0, 'active')"
    ), {"id": block_id, "type": type_})
    return block_id


def _schema(connection, database_id, name, type_, config):
    schema_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO property_schemas (id, database_id, name, type, position, config, \"group\") "
        "VALUES (:id, :db, :name, :type, 1.0, CAST(:config AS jsonb), 'Standard')"
    ), {"id": schema_id, "db": database_id, "name": name, "type": type_, "config": json.dumps(config)})
    return schema_id


def _value(connection, page_id, schema_id, value):
    value_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO property_values (id, page_id, property_schema_id, value) "
        "VALUES (:id, :page, :schema, CAST(:value AS jsonb))"
    ), {"id": value_id, "page": page_id, "schema": schema_id, "value": json.dumps(value)})
    return value_id


def _read(connection, table, column, row_id):
    return connection.scalar(sa.text(f"SELECT {column} FROM {table} WHERE id = :id"), {"id": row_id})


def test_migrate_converts_configs_values_and_views(connection):
    database = _block(connection, "database")
    entry = _block(connection, "page")
    status = _schema(connection, database, "Status", "select", {"options": ["Value A", "Value B"]})
    kept = _value(connection, entry, status, {"option": "Value B"})
    orphan = _value(connection, _block(connection, "page"), status, {"option": "Gone"})
    view_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO database_views (id, database_id, position, filter_groups) "
        "VALUES (:id, :db, 1.0, CAST(:groups AS jsonb))"
    ), {"id": view_id, "db": database, "groups": json.dumps([
        {"conjunction": "and", "filters": [
            {"schemaId": str(status), "operator": "eq", "value": "Value A"},
        ]},
    ])})

    report = migration.migrate(connection)

    options = _read(connection, "property_schemas", "config", status)["options"]
    by_label = {o["label"]: o["id"] for o in options}
    assert set(by_label) == {"Value A", "Value B"}
    assert _read(connection, "property_values", "value", kept) == {"option": by_label["Value B"]}
    assert _read(connection, "property_values", "value", orphan) is None
    groups = _read(connection, "database_views", "filter_groups", view_id)
    assert groups[0]["filters"][0]["value"] == by_label["Value A"]
    assert any("Gone" in line for line in report.dropped)


def test_migrate_gives_both_sides_of_a_bilateral_relation_the_same_nuance_ids(connection):
    first = _block(connection, "database")
    second = _block(connection, "database")
    nuance = {"enabled": True, "options": [{"label": "Value A"}, {"label": "Value B"}]}
    source = _schema(connection, first, "Link", "relation", {
        "direction": "bilateral", "target_database_id": str(second),
        "mirror_property_name": "Link Back", "nuance": nuance,
    })
    mirror = _schema(connection, second, "Link Back", "relation", {
        "direction": "bilateral", "target_database_id": str(first),
        "mirror_property_name": "Link", "nuance": nuance,
    })
    linked = str(uuid.uuid4())
    value = _value(connection, _block(connection, "page"), source, {
        "related_ids": [linked], "nuances": {linked: "Value A"},
    })

    migration.migrate(connection)

    source_options = _read(connection, "property_schemas", "config", source)["nuance"]["options"]
    mirror_options = _read(connection, "property_schemas", "config", mirror)["nuance"]["options"]
    assert source_options == mirror_options
    assert _read(connection, "property_values", "value", value)["nuances"] == {
        linked: source_options[0]["id"],
    }


def test_migrate_twice_changes_nothing(connection):
    database = _block(connection, "database")
    status = _schema(connection, database, "Status", "select", {"options": ["Value A"]})
    value = _value(connection, _block(connection, "page"), status, {"option": "Value A"})

    migration.migrate(connection)
    first = (_read(connection, "property_schemas", "config", status),
             _read(connection, "property_values", "value", value))
    migration.migrate(connection)

    assert (_read(connection, "property_schemas", "config", status),
            _read(connection, "property_values", "value", value)) == first


def test_downgrade_is_refused():
    with pytest.raises(NotImplementedError):
        migration.downgrade()
