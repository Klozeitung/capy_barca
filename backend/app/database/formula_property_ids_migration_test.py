"""
Tests for revision 0027, which stores the ``prop()`` references of formulas as
property ids.

The scanner is tested as pure functions; ``migrate`` and ``revert`` run
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
    / "alembic" / "versions" / "0027_formula_property_ids.py"
)
_spec = importlib.util.spec_from_file_location("migration_0027", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

ID_A = "00000000-0000-0000-0000-00000000000a"
ID_B = "00000000-0000-0000-0000-00000000000b"
PROPERTIES = [(ID_A, "Amount"), (ID_B, "Label X")]


# ─── Scanning ────────────────────────────────────────────────────────────────


def test_revision_follows_the_relation_timeline_revision():
    assert migration.revision == "a7d9c8y00027"
    assert migration.down_revision == "z6c8b7x90026"


def test_names_become_ids_in_either_quote():
    expression = "prop('Amount') * 2 + len(prop(\"Label X\"))"
    assert migration.to_ids(expression, PROPERTIES) == (
        f"prop('{ID_A}') * 2 + len(prop(\"{ID_B}\"))"
    )


def test_ids_become_names_again():
    stored = f"prop('{ID_A}') * 2 + len(prop(\"{ID_B}\"))"
    assert migration.to_names(stored, PROPERTIES) == (
        "prop('Amount') * 2 + len(prop(\"Label X\"))"
    )


def test_only_prop_arguments_are_rewritten():
    expression = "if(prop('Amount') == 'Amount', concat('Amount', 'x'), 'y')"
    assert migration.to_ids(expression, PROPERTIES) == (
        f"if(prop('{ID_A}') == 'Amount', concat('Amount', 'x'), 'y')"
    )


@pytest.mark.parametrize("expression", [
    "prop('Missing') + 1",
    "prop(concat('Amo', 'unt'))",
    f"prop('{ID_A}') + 1",
    "prop('Amount') # comment",
    "prop('Amount",
    "",
])
def test_what_names_no_property_or_does_not_scan_stays(expression):
    assert migration.to_ids(expression, PROPERTIES) == expression


def test_prop_is_matched_case_insensitively_and_escapes_survive():
    properties = [(ID_A, "It's")]
    expression = "PROP('It\\'s') + Prop(\"It's\")"
    assert migration.to_ids(expression, properties) == (
        f"PROP('{ID_A}') + Prop(\"{ID_A}\")"
    )
    assert migration.to_names(migration.to_ids(expression, properties), properties) == (
        "PROP('It\\'s') + Prop(\"It's\")"
    )


def test_a_name_spelled_like_an_id_is_read_as_that_id():
    properties = [(ID_A, "Amount"), (ID_B, ID_A)]
    assert migration.to_ids(f"prop('{ID_A}')", properties) == f"prop('{ID_A}')"


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


def _database(connection):
    block_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position, state) VALUES (:id, 'database', 1.0, 'active')"
    ), {"id": block_id})
    return block_id


def _schema(connection, database_id, name, type_, config=None):
    schema_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO property_schemas (id, database_id, name, type, position, config, \"group\") "
        "VALUES (:id, :db, :name, :type, 1.0, CAST(:config AS jsonb), 'Standard')"
    ), {
        "id": schema_id, "db": database_id, "name": name, "type": type_,
        "config": json.dumps(config) if config is not None else None,
    })
    return schema_id


def _config(connection, schema_id):
    return connection.scalar(
        sa.text("SELECT config FROM property_schemas WHERE id = :id"), {"id": schema_id},
    )


def test_migrate_binds_each_formula_to_its_own_database(connection):
    first, second = _database(connection), _database(connection)
    amount_first = _schema(connection, first, "Amount", "number")
    amount_second = _schema(connection, second, "Amount", "number")
    formula_first = _schema(connection, first, "Double", "formula", {
        "expression": "prop('Amount') * 2", "format": "plain",
    })
    formula_second = _schema(connection, second, "Double", "formula", {
        "expression": "prop('Amount') * 2",
    })
    unknown = _schema(connection, second, "Other", "formula", {"expression": "prop('Gone')"})
    text = _schema(connection, first, "Note", "text", {"expression": "prop('Amount')"})

    changed = migration.migrate(connection)

    assert changed >= 2
    assert _config(connection, formula_first) == {
        "expression": f"prop('{amount_first}') * 2", "format": "plain",
    }
    assert _config(connection, formula_second) == {"expression": f"prop('{amount_second}') * 2"}
    assert _config(connection, unknown) == {"expression": "prop('Gone')"}
    assert _config(connection, text) == {"expression": "prop('Amount')"}


def test_migrate_twice_changes_nothing_the_second_time(connection):
    database = _database(connection)
    _schema(connection, database, "Amount", "number")
    formula = _schema(connection, database, "Double", "formula", {"expression": "prop('Amount')"})

    migration.migrate(connection)
    first = _config(connection, formula)
    migration.migrate(connection)

    assert _config(connection, formula) == first


def test_formulas_without_an_expression_are_left_alone(connection):
    database = _database(connection)
    empty = _schema(connection, database, "Empty", "formula", {"expression": ""})
    bare = _schema(connection, database, "Bare", "formula", {})
    missing = _schema(connection, database, "Missing", "formula")

    migration.migrate(connection)

    assert _config(connection, empty) == {"expression": ""}
    assert _config(connection, bare) == {}
    assert _config(connection, missing) is None


def test_downgrade_turns_ids_back_into_names(connection):
    database = _database(connection)
    amount = _schema(connection, database, "Amount", "number")
    formula = _schema(connection, database, "Double", "formula", {
        "expression": f"prop('{amount}') * 2",
    })

    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.downgrade()

    assert _config(connection, formula) == {"expression": "prop('Amount') * 2"}
