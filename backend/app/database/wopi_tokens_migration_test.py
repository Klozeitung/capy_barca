"""
Tests for revision 0023, which creates the registry of issued WOPI tokens.

The session fixture has already migrated the test database to head, so the
table is inspected as it stands; the downgrade and upgrade are exercised
inside a transaction that is always rolled back.
"""
import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0023_wopi_tokens.py"
_spec = importlib.util.spec_from_file_location("migration_0023", _PATH)
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


def _seed_owner_and_block(connection) -> tuple[uuid.UUID, uuid.UUID]:
    user_id, block_id = uuid.uuid4(), uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO users (id, username, password_hash, role, is_active, date_format, created_at) "
        "VALUES (:id, :name, 'x', 'member', true, 'DD.MM.YYYY', :stamp)"
    ), {"id": user_id, "name": f"user-{user_id.hex[:8]}", "stamp": _START})
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position, state, created_at, updated_at) "
        "VALUES (:id, 'page', 1.0, 'active', :stamp, :stamp)"
    ), {"id": block_id, "stamp": _START})
    return user_id, block_id


def _insert_token(connection, user_id, block_id, session_key=None) -> uuid.UUID:
    jti = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO wopi_tokens "
        "(jti, user_id, block_id, file_uuid, session_key, issued_at, expires_at) "
        "VALUES (:jti, :user, :block, :file, :session, :issued, :expires)"
    ), {
        "jti": jti, "user": user_id, "block": block_id, "file": uuid.uuid4(),
        "session": session_key, "issued": _START, "expires": _START + timedelta(days=1),
    })
    return jti


def _insert_session(connection, user_id) -> str:
    key = uuid.uuid4().hex + uuid.uuid4().hex
    connection.execute(sa.text(
        "INSERT INTO sessions (token, created_at, expires_at, user_id) "
        "VALUES (:token, :created, :expires, :user)"
    ), {
        "token": key, "created": _START.replace(tzinfo=None),
        "expires": (_START + timedelta(days=7)).replace(tzinfo=None), "user": user_id,
    })
    return key


def _token_count(connection) -> int:
    return connection.execute(sa.text("SELECT count(*) FROM wopi_tokens")).scalar_one()


def test_revision_follows_the_sibling_positions_revision():
    assert migration.revision == "w3z5y4u60023"
    assert migration.down_revision == "v2y4x3t50022"


def test_table_has_the_registry_columns(test_engine):
    columns = {c["name"]: c for c in sa.inspect(test_engine).get_columns("wopi_tokens")}
    assert set(columns) == {
        "jti", "user_id", "block_id", "file_uuid", "session_key", "issued_at", "expires_at",
    }
    assert [name for name, column in columns.items() if column["nullable"]] == ["session_key"]


def test_lookups_are_indexed(test_engine):
    indexed = {
        tuple(index["column_names"])
        for index in sa.inspect(test_engine).get_indexes("wopi_tokens")
    }
    assert ("user_id",) in indexed
    assert ("session_key",) in indexed
    assert ("expires_at",) in indexed


def test_deleting_the_account_removes_its_tokens(connection):
    user_id, block_id = _seed_owner_and_block(connection)
    _insert_token(connection, user_id, block_id)

    connection.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": user_id})

    assert _token_count(connection) == 0


def test_deleting_the_block_removes_its_tokens(connection):
    user_id, block_id = _seed_owner_and_block(connection)
    _insert_token(connection, user_id, block_id)

    connection.execute(sa.text("DELETE FROM blocks WHERE id = :id"), {"id": block_id})

    assert _token_count(connection) == 0


def test_deleting_the_session_removes_only_its_tokens(connection):
    user_id, block_id = _seed_owner_and_block(connection)
    ended = _insert_session(connection, user_id)
    other = _insert_session(connection, user_id)
    _insert_token(connection, user_id, block_id, ended)
    kept = _insert_token(connection, user_id, block_id, other)

    connection.execute(sa.text("DELETE FROM sessions WHERE token = :key"), {"key": ended})

    remaining = connection.execute(sa.text("SELECT jti FROM wopi_tokens")).scalars().all()
    assert remaining == [kept]


def test_downgrade_drops_the_table_and_upgrade_restores_it(connection):
    _run(connection, migration.downgrade)
    assert "wopi_tokens" not in sa.inspect(connection).get_table_names()

    _run(connection, migration.upgrade)
    assert "wopi_tokens" in sa.inspect(connection).get_table_names()
