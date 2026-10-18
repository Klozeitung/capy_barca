"""
Tests for the shared conftest.py fixtures.

The isolated_db fixture is autouse, so every test in this file already runs
against an emptied PostgreSQL test database. The tests here verify that the
fixtures do what they promise: the right database, a migrated schema, and
full isolation between test invocations.
"""
import os

from sqlalchemy import inspect, text

import app.session.session as s
from app.blocks.models import WORKSPACE_ROOT_ID
from app.session.session import SessionRecord


def test_session_local_uses_postgresql_in_tests():
    """The patched SessionLocal must connect to PostgreSQL."""
    with s.SessionLocal() as db:
        dialect = db.bind.dialect.name
    assert dialect == "postgresql"


def test_session_local_targets_the_test_database():
    """Every session reaches the database named by TEST_DATABASE_URL."""
    with s.SessionLocal() as db:
        name = db.execute(text("SELECT current_database()")).scalar_one()
    assert name.endswith("_test")


def test_database_url_is_redirected_to_the_test_database():
    """
    Application code that reads DATABASE_URL directly must see the test
    database too, so nothing can reach another database by bypassing the
    patched session factories.
    """
    assert os.environ["DATABASE_URL"] == os.environ["TEST_DATABASE_URL"]


def test_schema_is_built_by_the_migrations():
    """The schema comes from Alembic, so the version table is present."""
    with s.SessionLocal() as db:
        table_names = inspect(db.bind).get_table_names()
        version = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert "alembic_version" in table_names
    assert version


def test_sessions_table_exists():
    """The SessionRecord table must exist before each test."""
    with s.SessionLocal() as db:
        table_names = inspect(db.bind).get_table_names()
    assert SessionRecord.__tablename__ in table_names


def test_database_is_empty_at_test_start():
    """No tokens should be present at the start of a test."""
    with s.SessionLocal() as db:
        count = db.query(SessionRecord).count()
    assert count == 0


def test_seeded_rows_are_cleared_as_well():
    """
    Migration 0002 seeds the workspace root block. The isolation fixture
    removes it with every other row, so tests start from the same empty state
    regardless of what the migrations insert.
    """
    from app.blocks.models import Block

    with s.SessionLocal() as db:
        assert db.get(Block, WORKSPACE_ROOT_ID) is None


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
    import asyncio

    from app.session.deps import get_db

    async def _run():
        generator = get_db()
        db = await generator.__anext__()
        try:
            assert db.bind.dialect.name == "postgresql"
            name = db.execute(text("SELECT current_database()")).scalar_one()
            assert name.endswith("_test")
        finally:
            await generator.aclose()

    asyncio.run(_run())


def test_media_files_table_exists():
    """
    The media table is created by its own migration. If it is missing, the
    tests that need it fail somewhere far from the cause.
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


# ─── Acting user ──────────────────────────────────────────────────────────────


def test_http_client_acts_as_admin_by_default(http_client):
    """The user list is admin-only, so a 200 proves the admin identity."""
    response = http_client.get("/api/users")
    assert response.status_code == 200


def test_member_user_is_a_persisted_member(member_user):
    from app.users.model import User

    with s.SessionLocal() as db:
        stored = db.get(User, member_user.id)
    assert stored is not None
    assert stored.role == "member"
    assert stored.is_active is True


def test_login_as_switches_the_identity_of_the_running_client(
    http_client, login_as, member_user,
):
    login_as(member_user)
    response = http_client.get("/api/users/me")
    assert response.status_code == 200
    assert response.json()["id"] == str(member_user.id)
    assert response.json()["role"] == "member"


def test_member_client_is_refused_where_only_admins_are_admitted(member_client):
    """
    The point of the member identity: an admin-only endpoint answers 403,
    which no test running as the default admin could ever observe.
    """
    response = member_client.get("/api/users")
    assert response.status_code == 403


def test_login_as_admin_user_switches_back(http_client, login_as, admin_user, member_user):
    login_as(member_user)
    assert http_client.get("/api/users").status_code == 403
    login_as(admin_user)
    assert http_client.get("/api/users").status_code == 200


def test_member_client_does_not_leak_into_the_next_test(http_client):
    """Runs after the member tests above and still sees the default admin."""
    assert http_client.get("/api/users").status_code == 200


def test_suite_supplies_a_signing_secret():
    """The application refuses to start without one (see app.main.lifespan)."""
    assert os.environ.get("SECRET_KEY", "").strip()


# ─── Query counter ────────────────────────────────────────────────────────────


def test_count_queries_counts_statements_inside_the_block(count_queries):
    with count_queries() as counter:
        with s.SessionLocal() as db:
            db.execute(text("SELECT 1"))
            db.execute(text("SELECT 2"))
    assert counter.count == 2


def test_count_queries_stops_counting_after_the_block(count_queries):
    with count_queries() as counter:
        pass
    with s.SessionLocal() as db:
        db.execute(text("SELECT 1"))
    assert counter.count == 0
