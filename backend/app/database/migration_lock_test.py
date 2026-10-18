"""
Tests for the lock that serialises concurrent Alembic runs.

The unit tests use a fake connection, so they pin which statement is issued
for which database. The last test runs two lock holders against the test
database to show that the second really waits for the first.
"""
import threading
import time

import pytest
from sqlalchemy import create_engine

from app.database.migration_lock import MIGRATION_LOCK_KEY, acquire_migration_lock


class _Dialect:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeConnection:
    def __init__(self, dialect: str) -> None:
        self.dialect = _Dialect(dialect)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params))


def test_postgresql_takes_exactly_one_transaction_scoped_lock():
    connection = _FakeConnection("postgresql")

    assert acquire_migration_lock(connection) is True

    assert connection.calls == [
        ("SELECT pg_advisory_xact_lock(:key)", {"key": MIGRATION_LOCK_KEY}),
    ]


@pytest.mark.parametrize("dialect", ["sqlite", "mysql"])
def test_other_databases_take_no_lock(dialect):
    connection = _FakeConnection(dialect)

    assert acquire_migration_lock(connection) is False

    assert connection.calls == []


def test_key_fits_a_signed_64_bit_integer():
    assert -(2**63) <= MIGRATION_LOCK_KEY < 2**63


def test_second_holder_waits_until_the_first_commits(test_engine):
    engine = create_engine(test_engine.url)
    events: list[str] = []
    first_has_lock = threading.Event()

    def first():
        with engine.begin() as connection:
            acquire_migration_lock(connection)
            first_has_lock.set()
            time.sleep(0.5)
            events.append("first done")

    def second():
        first_has_lock.wait(5)
        with engine.begin() as connection:
            acquire_migration_lock(connection)
            events.append("second locked")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    engine.dispose()

    assert events == ["first done", "second locked"]
