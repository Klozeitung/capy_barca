"""
Tests for the shared conftest.py fixture.

The isolated_db fixture is autouse, so every test in this file already
runs inside an isolated in-memory SQLite database. The tests here verify
that the fixture does what it promises: correct engine dialect, table
presence, and full isolation between test invocations.
"""
from sqlalchemy import inspect, text

import app.session.session as s
from app.session.session import SessionRecord


def test_session_local_uses_sqlite_in_tests():
    """The patched SessionLocal must connect to SQLite, not PostgreSQL."""
    with s.SessionLocal() as db:
        dialect = db.bind.dialect.name
    assert dialect == "sqlite"


def test_sessions_table_exists():
    """The SessionRecord table must be created by the fixture before each test."""
    with s.SessionLocal() as db:
        table_names = inspect(db.bind).get_table_names()
    assert SessionRecord.__tablename__ in table_names


def test_database_is_empty_at_test_start():
    """No tokens should be present at the start of a test."""
    with s.SessionLocal() as db:
        count = db.query(SessionRecord).count()
    assert count == 0


def test_tokens_written_in_test_are_visible_within_same_test():
    token = s.create_token()
    with s.SessionLocal() as db:
        record = db.get(SessionRecord, s._hash_token(token))
    assert record is not None


def test_isolation_between_two_sequential_writes():
    """
    Simulate what would happen across two tests: each starts with an empty
    table. This single test verifies the contract by asserting the count
    equals exactly what was written here, with no leftover from previous
    test functions.
    """
    s.create_token()
    s.create_token()
    with s.SessionLocal() as db:
        count = db.query(SessionRecord).count()
    assert count == 2


def test_revoked_token_is_absent_from_db():
    token = s.create_token()
    s.revoke_token(token)
    with s.SessionLocal() as db:
        record = db.get(SessionRecord, s._hash_token(token))
    assert record is None
