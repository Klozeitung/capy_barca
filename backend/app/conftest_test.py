"""
Tests for the shared conftest.py fixture.

The isolated_db fixture is autouse, so every test in this file already
runs inside an isolated in-memory SQLite database. The tests here verify
that the fixture does what it promises: correct engine dialect, table
presence, and full isolation between test invocations.
"""
from sqlalchemy import inspect, text

import app.session.session as s
from app.blocks.models import WORKSPACE_ROOT_ID
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


# ─── Shared session factory ───────────────────────────────────────────────────


def test_deps_get_db_uses_the_isolated_session():
    """
    Every router reaches the database through ``app.session.deps.get_db``, the
    block router included — it re-exports the dependency rather than defining
    its own. The fixture therefore has to redirect that module's SessionLocal,
    and this test fails if that patch is ever dropped.
    """
    from app.session.deps import get_db

    generator = get_db()
    db = next(generator)
    try:
        assert db.bind.dialect.name == "sqlite"
    finally:
        generator.close()


def test_media_files_table_exists():
    """
    Every model has to be imported by conftest before create_all runs, or its
    table simply is not there and the tests that need it fail somewhere far
    from the cause.
    """
    from app.media.model import MediaFile

    with s.SessionLocal() as db:
        table_names = inspect(db.bind).get_table_names()
    assert MediaFile.__tablename__ in table_names


def test_http_client_authenticates_the_block_router(http_client):
    """
    One dependency override is enough for every router.

    The block router used to need a second mechanism as well — a monkeypatched
    ``app.blocks.router.validate_token`` for its own module-local auth path.
    That path is gone, and this test pins the replacement: the shared override
    alone gets an authenticated request through.
    """
    response = http_client.get(f"/api/blocks/{WORKSPACE_ROOT_ID}")
    assert response.status_code == 200
