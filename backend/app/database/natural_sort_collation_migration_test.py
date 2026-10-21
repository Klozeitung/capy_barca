"""
Tests for revision 0029, which creates the ``natural_sort`` collation.

They run against the PostgreSQL test database, whose schema the session
fixture has already migrated to head, inside a transaction that is always
rolled back.
"""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0029_natural_sort_collation.py"
)
_spec = importlib.util.spec_from_file_location("migration_0029", _PATH)
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


def _sorted(connection, values: list[str]) -> list[str]:
    rows = connection.execute(sa.text(
        "SELECT v FROM unnest(CAST(:values AS text[])) AS t(v) "
        f"ORDER BY v COLLATE {migration.COLLATION}"
    ), {"values": values})
    return [row.v for row in rows]


def _exists(connection) -> bool:
    return bool(connection.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM pg_collation WHERE collname = :name)"
    ), {"name": migration.COLLATION}))


def test_revision_follows_the_relation_chip_icons_revision():
    assert migration.revision == "c9f1e0a20029"
    assert migration.down_revision == "b8e0d9z10028"


def test_the_collation_exists_after_the_migrations(connection):
    assert _exists(connection)


def test_digit_runs_sort_as_numbers(connection):
    assert _sorted(connection, ["20", "3", "1", "19", "10", "2"]) == [
        "1", "2", "3", "10", "19", "20",
    ]
    assert _sorted(connection, ["10. Item", "2. Item", "1. Item"]) == [
        "1. Item", "2. Item", "10. Item",
    ]


def test_letters_sort_as_in_a_dictionary(connection):
    assert _sorted(connection, ["b", "B", "a", "A"]) == ["a", "A", "b", "B"]


def test_creating_twice_is_harmless(connection):
    migration.create(connection)
    assert _exists(connection)


def test_without_icu_the_migration_stops(monkeypatch, connection):
    monkeypatch.setattr(migration, "has_icu", lambda _connection: False)
    with pytest.raises(RuntimeError, match="ICU"):
        migration.create(connection)


def test_downgrade_drops_the_collation(connection):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.downgrade()

    assert not _exists(connection)
