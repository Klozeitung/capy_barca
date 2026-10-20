"""
Tests for revision 0025, which creates the settings of whole databases.

The session fixture has already migrated the test database to head, so the
table is inspected as it stands; the downgrade and upgrade are exercised
inside a transaction that is always rolled back.
"""
import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0025_database_settings.py"
_spec = importlib.util.spec_from_file_location("migration_0025", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

_START = datetime(2000, 7, 28, tzinfo=timezone.utc)


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


def _insert_database(connection) -> uuid.UUID:
    block_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position, state, created_at, updated_at) "
        "VALUES (:id, 'database', 1.0, 'active', :stamp, :stamp)"
    ), {"id": block_id, "stamp": _START})
    return block_id


def test_revision_follows_the_recent_icons_revision():
    assert migration.revision == "y5b7a6w80025"
    assert migration.down_revision == "x4a6z5v70024"


def test_table_has_the_settings_columns(test_engine):
    columns = {c["name"]: c for c in sa.inspect(test_engine).get_columns("database_settings")}
    assert set(columns) == {"database_id", "default_entry_icon", "updated_at"}
    assert columns["default_entry_icon"]["nullable"] is True
    assert columns["updated_at"]["nullable"] is False


def test_a_row_without_icon_stores_none_and_a_timestamp(connection):
    database_id = _insert_database(connection)
    connection.execute(
        sa.text("INSERT INTO database_settings (database_id) VALUES (:id)"), {"id": database_id},
    )
    icon, updated_at = connection.execute(
        sa.text("SELECT default_entry_icon, updated_at FROM database_settings WHERE database_id = :id"),
        {"id": database_id},
    ).one()
    assert icon is None
    assert updated_at is not None


def test_deleting_the_database_removes_its_settings(connection):
    database_id = _insert_database(connection)
    connection.execute(
        sa.text("INSERT INTO database_settings (database_id, default_entry_icon) VALUES (:id, 'mdi:alpha-a')"),
        {"id": database_id},
    )

    connection.execute(sa.text("DELETE FROM blocks WHERE id = :id"), {"id": database_id})

    count = connection.execute(sa.text("SELECT count(*) FROM database_settings")).scalar_one()
    assert count == 0


def test_downgrade_drops_the_table_and_upgrade_restores_it(connection):
    _run(connection, migration.downgrade)
    assert "database_settings" not in sa.inspect(connection).get_table_names()

    _run(connection, migration.upgrade)
    assert "database_settings" in sa.inspect(connection).get_table_names()
