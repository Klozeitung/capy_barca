"""
Tests for revision 0031, which creates ``alerts`` and limits a database to
one property of type ``alerts``.

They run against the PostgreSQL test database, whose schema the session
fixture has already migrated to head, inside a transaction that is always
rolled back.
"""
import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0031_alerts.py"
)
_spec = importlib.util.spec_from_file_location("migration_0031", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


@pytest.fixture
def connection(test_engine):
    """A connection inside a transaction that is always rolled back."""
    with test_engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


def _run(connection, step) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        step()


def _tables(connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


def _index_names(connection, table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(connection).get_indexes(table)}


def _database(connection) -> uuid.UUID:
    block_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position) VALUES (:id, 'database', 1.0)"
    ), {"id": block_id})
    return block_id


def _schema(connection, database_id, name: str, type_: str) -> uuid.UUID:
    schema_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO property_schemas (id, database_id, name, type, position) "
        "VALUES (:id, :db, :name, :type, 1.0)"
    ), {"id": schema_id, "db": database_id, "name": name, "type": type_})
    return schema_id


def test_revision_follows_the_api_settings_revision():
    assert migration.revision == "e1b3a2c40031"
    assert migration.down_revision == "d0a2f1b30030"


def test_the_table_and_its_indexes_exist_after_the_migrations(connection):
    assert migration.TABLE in _tables(connection)
    columns = {c["name"] for c in sa.inspect(connection).get_columns(migration.TABLE)}
    # Later revisions add columns (0032: kind); these are the ones 0031 made.
    assert {
        "id", "block_id", "schema_id", "user_id", "fire_at", "title",
        "fired_at", "delivered", "dismissed_at", "created_at", "updated_at",
    } <= columns
    assert {"ix_alerts_due", "ix_alerts_user_block", "ix_alerts_schema_id"} <= _index_names(
        connection, migration.TABLE,
    )
    assert migration.SINGLE_ALERTS_INDEX in _index_names(connection, "property_schemas")


def test_a_second_alerts_property_in_one_database_is_refused(connection):
    database_id = _database(connection)
    _schema(connection, database_id, "Reminders", "alerts")
    _schema(connection, database_id, "Notes", "text")
    _schema(connection, _database(connection), "Reminders", "alerts")

    with pytest.raises(sa.exc.IntegrityError):
        with connection.begin_nested():
            _schema(connection, database_id, "Second", "alerts")


def test_downgrade_removes_table_and_index_and_upgrade_restores_them(connection):
    _run(connection, migration.downgrade)
    assert migration.TABLE not in _tables(connection)
    assert migration.SINGLE_ALERTS_INDEX not in _index_names(connection, "property_schemas")

    _run(connection, migration.upgrade)
    assert migration.TABLE in _tables(connection)
    assert migration.SINGLE_ALERTS_INDEX in _index_names(connection, "property_schemas")
