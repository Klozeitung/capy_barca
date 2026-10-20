"""
Tests for revision 0024, which adds the recently used icons to the users.

The session fixture has already migrated the test database to head, so the
column is inspected as it stands; the downgrade and upgrade are exercised
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

_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0024_recent_icons.py"
_spec = importlib.util.spec_from_file_location("migration_0024", _PATH)
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


def _insert_user_without_icons(connection) -> uuid.UUID:
    """Insert a user the way rows existed before this revision: no icon list."""
    user_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO users (id, username, password_hash, role, is_active, date_format, created_at) "
        "VALUES (:id, :name, 'x', 'member', true, 'DD.MM.YYYY', :stamp)"
    ), {"id": user_id, "name": f"user-{user_id.hex[:8]}", "stamp": _START})
    return user_id


def _icons_of(connection, user_id):
    return connection.execute(
        sa.text("SELECT recent_icons FROM users WHERE id = :id"), {"id": user_id},
    ).scalar_one()


def test_revision_follows_the_wopi_tokens_revision():
    assert migration.revision == "x4a6z5v70024"
    assert migration.down_revision == "w3z5y4u60023"


def test_column_is_a_required_jsonb(test_engine):
    columns = {c["name"]: c for c in sa.inspect(test_engine).get_columns("users")}
    column = columns["recent_icons"]
    assert column["nullable"] is False
    assert type(column["type"]).__name__ == "JSONB"


def test_a_row_inserted_without_icons_gets_the_empty_list(connection):
    user_id = _insert_user_without_icons(connection)
    assert _icons_of(connection, user_id) == []


def test_upgrade_backfills_existing_rows_with_the_empty_list(connection):
    _run(connection, migration.downgrade)
    user_id = _insert_user_without_icons(connection)

    _run(connection, migration.upgrade)

    assert _icons_of(connection, user_id) == []


def test_downgrade_drops_the_column(connection):
    _run(connection, migration.downgrade)
    columns = {c["name"] for c in sa.inspect(connection).get_columns("users")}
    assert "recent_icons" not in columns
