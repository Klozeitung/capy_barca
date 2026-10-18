"""
Tests for revision 0021, which adds access levels to block permissions.

Each test steps the already migrated test database back to the shape of the
previous revision inside a transaction, applies the revision again, and rolls
everything back at the end. PostgreSQL runs DDL transactionally, so the shared
schema is left exactly as the session fixture built it.
"""
import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0021_permission_levels.py"
_spec = importlib.util.spec_from_file_location("migration_0021", _PATH)
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


def _columns(connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(connection).get_columns(table)}


def _seed_previous_shape(connection) -> tuple[uuid.UUID, uuid.UUID]:
    """A block with a whitelist row and one grant, written without levels."""
    block_id, user_id = uuid.uuid4(), uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position, state) VALUES (:id, 'page', 1.0, 'active')"
    ), {"id": block_id})
    connection.execute(sa.text(
        "INSERT INTO block_permissions (block_id, mode) VALUES (:id, 'whitelist')"
    ), {"id": block_id})
    connection.execute(sa.text(
        "INSERT INTO block_permission_grants (block_id, user_id) VALUES (:id, :user)"
    ), {"id": block_id, "user": user_id})
    return block_id, user_id


def test_revision_follows_the_option_ids_revision():
    assert migration.revision == "u1x3w2s40021"
    assert migration.down_revision == "t0w2v1r30020"


def test_downgrade_removes_both_columns(connection):
    _run(connection, migration.downgrade)
    assert "everyone_level" not in _columns(connection, "block_permissions")
    assert "level" not in _columns(connection, "block_permission_grants")


def test_upgrade_gives_existing_rows_write_access(connection):
    """Every account keeps exactly the access it had before the upgrade."""
    _run(connection, migration.downgrade)
    block_id, user_id = _seed_previous_shape(connection)

    _run(connection, migration.upgrade)

    assert connection.scalar(sa.text(
        "SELECT everyone_level FROM block_permissions WHERE block_id = :id"
    ), {"id": block_id}) == "write"
    assert connection.scalar(sa.text(
        "SELECT level FROM block_permission_grants WHERE block_id = :id AND user_id = :user"
    ), {"id": block_id, "user": user_id}) == "write"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE block_permissions SET everyone_level = 'admin'",
        "UPDATE block_permission_grants SET level = 'owner'",
    ],
)
def test_upgrade_restricts_the_levels(connection, statement):
    _run(connection, migration.downgrade)
    _seed_previous_shape(connection)
    _run(connection, migration.upgrade)

    savepoint = connection.begin_nested()
    with pytest.raises(sa.exc.IntegrityError):
        connection.execute(sa.text(statement))
    savepoint.rollback()


def test_upgrade_accepts_read(connection):
    _run(connection, migration.downgrade)
    block_id, _ = _seed_previous_shape(connection)
    _run(connection, migration.upgrade)

    connection.execute(sa.text("UPDATE block_permissions SET everyone_level = 'read'"))
    connection.execute(sa.text("UPDATE block_permission_grants SET level = 'read'"))

    assert connection.scalar(sa.text(
        "SELECT everyone_level FROM block_permissions WHERE block_id = :id"
    ), {"id": block_id}) == "read"
