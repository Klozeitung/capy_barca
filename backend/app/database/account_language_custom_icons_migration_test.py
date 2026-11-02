"""
Tests for revision 0033, which adds ``users.language`` and ``custom_icons``.

They run against the PostgreSQL test database, migrated to head, inside a
transaction that is always rolled back.
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
    / "alembic" / "versions" / "0033_account_language_custom_icons.py"
)
_spec = importlib.util.spec_from_file_location("migration_0033", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


@pytest.fixture
def connection(test_engine):
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


def _insert_user(connection, username: str = "person-a", language=None) -> uuid.UUID:
    user_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO users (id, username, password_hash, language) VALUES (:id, :n, 'x', :l)"
    ), {"id": user_id, "n": username, "l": language})
    return user_id


def _insert_icon(connection, uploaded_by=None, stored_name: str = "a.png") -> uuid.UUID:
    icon_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO custom_icons (id, name, stored_name, uploaded_by) VALUES (:id, 'Icon A', :s, :u)"
    ), {"id": icon_id, "s": stored_name, "u": uploaded_by})
    return icon_id


def test_revision_follows_the_calendar_alerts_revision():
    assert migration.revision == "a3d5c4e60033"
    assert migration.down_revision == "f2c4b3d50032"


def test_language_starts_empty_and_takes_the_two_languages(connection):
    assert migration.LANGUAGE_COLUMN in _columns(connection, "users")
    connection.execute(sa.text(
        "INSERT INTO users (id, username, password_hash) VALUES (:id, 'person-a', 'x')"
    ), {"id": uuid.uuid4()})
    assert connection.scalar(sa.text("SELECT language FROM users")) is None
    _insert_user(connection, "person-b", "de")
    _insert_user(connection, "person-c", "en")


def test_an_unknown_language_is_refused(connection):
    with pytest.raises(sa.exc.IntegrityError):
        with connection.begin_nested():
            _insert_user(connection, "person-a", "fr")


def test_upgrade_leaves_existing_accounts_without_a_language(connection):
    _run(connection, migration.downgrade)
    connection.execute(sa.text(
        "INSERT INTO users (id, username, password_hash) VALUES (:id, 'person-a', 'x')"
    ), {"id": uuid.uuid4()})

    _run(connection, migration.upgrade)

    assert connection.scalar(sa.text("SELECT language FROM users")) is None


def test_deleting_the_uploader_keeps_the_icon(connection):
    user_id = _insert_user(connection)
    icon_id = _insert_icon(connection, uploaded_by=user_id)

    connection.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": user_id})

    row = connection.execute(sa.text(
        "SELECT id, uploaded_by FROM custom_icons"
    )).one()
    assert row.id == icon_id
    assert row.uploaded_by is None


def test_stored_names_are_unique(connection):
    _insert_icon(connection, stored_name="same.png")
    with pytest.raises(sa.exc.IntegrityError):
        with connection.begin_nested():
            _insert_icon(connection, stored_name="same.png")


def test_downgrade_removes_the_table_and_the_column(connection):
    _insert_icon(connection)
    _insert_user(connection, "person-a", "en")

    _run(connection, migration.downgrade)

    assert migration.LANGUAGE_COLUMN not in _columns(connection, "users")
    assert migration.ICONS_TABLE not in sa.inspect(connection).get_table_names()
