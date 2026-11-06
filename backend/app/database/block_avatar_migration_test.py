"""
Tests for revision 0034, which adds ``blocks.avatar``.

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
    / "alembic" / "versions" / "0034_block_avatar.py"
)
_spec = importlib.util.spec_from_file_location("migration_0034", _PATH)
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


def _columns(connection) -> set[str]:
    return {c["name"] for c in sa.inspect(connection).get_columns("blocks")}


def _insert_page(connection) -> uuid.UUID:
    block_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position) VALUES (:id, 'page', 1.0)"
    ), {"id": block_id})
    return block_id


def test_revision_follows_the_account_language_revision():
    assert migration.revision == "b4e6d5f70034"
    assert migration.down_revision == "a3d5c4e60033"


def test_new_pages_start_without_an_avatar(connection):
    assert migration.AVATAR_COLUMN in _columns(connection)
    block_id = _insert_page(connection)
    assert connection.scalar(
        sa.text("SELECT avatar FROM blocks WHERE id = :id"), {"id": block_id},
    ) is None


def test_upgrade_leaves_existing_pages_without_an_avatar(connection):
    _run(connection, migration.downgrade)
    block_id = _insert_page(connection)

    _run(connection, migration.upgrade)

    assert connection.scalar(
        sa.text("SELECT avatar FROM blocks WHERE id = :id"), {"id": block_id},
    ) is None


def test_downgrade_removes_the_column_and_keeps_the_page(connection):
    block_id = _insert_page(connection)
    connection.execute(sa.text(
        "UPDATE blocks SET avatar = '/static/uploads/avatars/x.png' WHERE id = :id"
    ), {"id": block_id})

    _run(connection, migration.downgrade)

    assert migration.AVATAR_COLUMN not in _columns(connection)
    assert connection.scalar(
        sa.text("SELECT count(*) FROM blocks WHERE id = :id"), {"id": block_id},
    ) == 1
