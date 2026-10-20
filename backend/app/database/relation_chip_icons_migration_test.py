"""
Tests for revision 0028, which adds entry icons to the relation chips stored
by formulas and rollups.

The rewrite is tested as pure functions; ``migrate`` and ``revert`` run
against the PostgreSQL test database, whose schema the session fixture has
already migrated to head, inside a transaction that is always rolled back.
"""
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0028_relation_chip_icons.py"
)
_spec = importlib.util.spec_from_file_location("migration_0028", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


def _chip(entry_id, **extra):
    return {"id": entry_id, "title": "Entry A", "database_id": "db", **extra}


# ─── Rewriting ───────────────────────────────────────────────────────────────


def test_revision_follows_the_formula_property_ids_revision():
    assert migration.revision == "b8e0d9z10028"
    assert migration.down_revision == "a7d9c8y00027"


def test_list_results_gain_the_icon_of_each_entry():
    value = {"result": [_chip("a"), _chip("b")], "relation": True}
    assert migration.with_icons(value, {"a": "mdi:star-outline"}) == {
        "result": [_chip("a", icon="mdi:star-outline"), _chip("b", icon=None)],
        "relation": True,
    }


def test_a_single_descriptor_gains_its_icon():
    value = {"result": _chip("a"), "relation": True}
    assert migration.with_icons(value, {"a": "mdi:star-outline"}) == {
        "result": _chip("a", icon="mdi:star-outline"), "relation": True,
    }


def test_a_descriptor_with_an_icon_keeps_it():
    value = {"result": [_chip("a", icon="mdi:flag-outline")], "relation": True}
    assert migration.with_icons(value, {"a": "mdi:star-outline"}) == value


@pytest.mark.parametrize("value", [
    None,
    "not a value",
    {"result": [_chip("a")]},
    {"result": 3, "relation": True},
    {"result": ["a", "b"], "relation": True},
    {"result": None, "relation": True},
])
def test_what_holds_no_chips_is_left_alone(value):
    assert migration.with_icons(value, {"a": "mdi:star-outline"}) == value
    assert migration.descriptor_ids(value) == []


def test_without_icons_undoes_with_icons():
    value = {"result": [_chip("a"), _chip("b")], "relation": True, "result_type": "list"}
    assert migration.without_icons(migration.with_icons(value, {"a": "mdi:star-outline"})) == value


# ─── Against the database ────────────────────────────────────────────────────


@pytest.fixture
def connection(test_engine):
    """A connection inside a transaction that is always rolled back."""
    with test_engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


def _block(connection, type_, icon=None):
    block_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position, state, icon) "
        "VALUES (:id, :type, 1.0, 'active', :icon)"
    ), {"id": block_id, "type": type_, "icon": icon})
    return block_id


def _schema(connection, database_id, type_):
    schema_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO property_schemas (id, database_id, name, type, position, config, \"group\") "
        "VALUES (:id, :db, :name, :type, 1.0, CAST('{}' AS jsonb), 'Standard')"
    ), {"id": schema_id, "db": database_id, "name": f"Property {schema_id.hex[:6]}", "type": type_})
    return schema_id


def _value(connection, schema_id, value):
    value_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO property_values (id, page_id, property_schema_id, value) "
        "VALUES (:id, :page, :schema, CAST(:value AS jsonb))"
    ), {
        "id": value_id, "page": _block(connection, "page"), "schema": schema_id,
        "value": json.dumps(value),
    })
    return value_id


def _read(connection, value_id):
    return connection.scalar(
        sa.text("SELECT value FROM property_values WHERE id = :id"), {"id": value_id},
    )


def test_migrate_adds_the_current_icons_to_formula_and_rollup_chips(connection):
    database = _block(connection, "database")
    with_icon = str(_block(connection, "page", icon="mdi:star-outline"))
    without_icon = str(_block(connection, "page"))
    gone = str(uuid.uuid4())
    formula = _schema(connection, database, "formula")
    rollup = _schema(connection, database, "rollup")
    text = _schema(connection, database, "text")
    listed = _value(connection, formula, {
        "result": [_chip(with_icon), _chip(without_icon), _chip(gone), _chip("not-an-id")],
        "relation": True,
    })
    single = _value(connection, rollup, {"result": _chip(with_icon), "relation": True})
    plain = _value(connection, text, {"result": [_chip(with_icon)], "relation": True})

    changed = migration.migrate(connection)

    assert changed >= 2
    assert _read(connection, listed)["result"] == [
        _chip(with_icon, icon="mdi:star-outline"), _chip(without_icon, icon=None),
        _chip(gone, icon=None), _chip("not-an-id", icon=None),
    ]
    assert _read(connection, single)["result"] == _chip(with_icon, icon="mdi:star-outline")
    assert _read(connection, plain) == {"result": [_chip(with_icon)], "relation": True}


def test_migrate_twice_changes_nothing_the_second_time(connection):
    database = _block(connection, "database")
    entry = str(_block(connection, "page", icon="mdi:star-outline"))
    formula = _schema(connection, database, "formula")
    value = _value(connection, formula, {"result": [_chip(entry)], "relation": True})

    migration.migrate(connection)
    first = _read(connection, value)
    migration.migrate(connection)

    assert _read(connection, value) == first


def test_downgrade_removes_the_icons(connection):
    database = _block(connection, "database")
    entry = str(_block(connection, "page", icon="mdi:star-outline"))
    formula = _schema(connection, database, "formula")
    value = _value(connection, formula, {
        "result": [_chip(entry, icon="mdi:star-outline")], "relation": True,
    })

    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.downgrade()

    assert _read(connection, value) == {"result": [_chip(entry)], "relation": True}
