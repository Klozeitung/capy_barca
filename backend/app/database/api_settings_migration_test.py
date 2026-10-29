"""
Tests for revision 0030, which creates ``app_settings`` and adds
``users.can_print``.

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
    / "alembic" / "versions" / "0030_api_settings.py"
)
_spec = importlib.util.spec_from_file_location("migration_0030", _PATH)
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
    return {c["name"] for c in sa.inspect(connection).get_columns(table)}


def _tables(connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


def _insert_user(connection, name: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO users (id, username, password_hash) VALUES (:id, :name, 'x')"
    ), {"id": user_id, "name": name})
    return user_id


def test_revision_follows_the_natural_sort_collation_revision():
    assert migration.revision == "d0a2f1b30030"
    assert migration.down_revision == "c9f1e0a20029"


def test_the_table_and_the_column_exist_after_the_migrations(connection):
    assert migration.TABLE in _tables(connection)
    assert _columns(connection, migration.TABLE) == {"key", "value", "updated_at"}
    assert migration.COLUMN in _columns(connection, "users")


def test_an_account_starts_without_the_print_permission(connection):
    user_id = _insert_user(connection, "person-a")
    assert connection.scalar(
        sa.text("SELECT can_print FROM users WHERE id = :id"), {"id": user_id}
    ) is False


def test_a_setting_stores_a_json_object_under_its_key(connection):
    connection.execute(sa.text(
        "INSERT INTO app_settings (key, value) VALUES ('setting-a', CAST(:v AS jsonb))"
    ), {"v": '{"url": "http://host-x:1234/print"}'})
    stored = connection.scalar(sa.text(
        "SELECT value FROM app_settings WHERE key = 'setting-a'"
    ))
    assert stored == {"url": "http://host-x:1234/print"}


def test_downgrade_removes_both_and_upgrade_restores_them(connection):
    _run(connection, migration.downgrade)
    assert migration.TABLE not in _tables(connection)
    assert migration.COLUMN not in _columns(connection, "users")

    _run(connection, migration.upgrade)
    assert migration.TABLE in _tables(connection)
    assert migration.COLUMN in _columns(connection, "users")


def test_upgrade_gives_existing_accounts_no_print_permission(connection):
    _run(connection, migration.downgrade)
    user_id = _insert_user(connection, "person-b")

    _run(connection, migration.upgrade)

    assert connection.scalar(
        sa.text("SELECT can_print FROM users WHERE id = :id"), {"id": user_id}
    ) is False
