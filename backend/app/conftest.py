"""
Shared pytest fixtures.

The suite runs against PostgreSQL, the database CapyBarca uses in production.
Tests therefore exercise the real column types (JSONB), the real foreign-key
enforcement and the real query planner instead of an approximation of them.

Test database
-------------
``TEST_DATABASE_URL`` names the database the suite may use. It is mandatory,
and its database name must end in ``_test``: the suite drops and recreates the
``public`` schema of that database at the start of every run and deletes every
row before every test, so it must never be pointed at real data. setup.sh
creates ``<POSTGRES_DB>_test`` next to the production database and passes the
URL in; CI starts a dedicated PostgreSQL service.

``DATABASE_URL`` is overwritten with the test URL before the application is
imported, so even code that bypasses the fixtures below cannot reach any other
database.

Schema
------
The schema is built by running the Alembic migrations, not from the models.
That is what an installation actually runs, so the tests see the same tables,
types and indexes as production.

Isolation
---------
Every test starts with all tables empty, including the rows that migrations
seed (the workspace root block). Tests that need the root create it, exactly
as they did before. Deleting rows is considerably faster than TRUNCATE for
tables this small and keeps the suite within the runtime of the previous
in-memory setup.
"""
import os
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

_TEST_DATABASE_SUFFIX = "_test"


def _resolve_test_database_url() -> str:
    """
    Return TEST_DATABASE_URL after checking that it is safe to wipe.

    Aborts the run when the variable is missing, when its database name lacks
    the ``_test`` suffix, or when it names the same database as DATABASE_URL.
    """
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.exit(
            "TEST_DATABASE_URL is not set. The test suite needs a PostgreSQL "
            "database whose name ends in '_test', for example "
            "postgresql://user:password@localhost:5432/capybarca_db_test",
            returncode=4,
        )
    database = make_url(url).database or ""
    if not database.endswith(_TEST_DATABASE_SUFFIX):
        pytest.exit(
            f"TEST_DATABASE_URL names the database {database!r}. The suite "
            f"deletes all data in the database it runs against and only accepts "
            f"names ending in {_TEST_DATABASE_SUFFIX!r}.",
            returncode=4,
        )
    configured = os.environ.get("DATABASE_URL", "").strip()
    if configured and (make_url(configured).database or "") == database:
        pytest.exit(
            "TEST_DATABASE_URL and DATABASE_URL name the same database. The "
            "suite refuses to run against the application's own database.",
            returncode=4,
        )
    return url


_TEST_DATABASE_URL = _resolve_test_database_url()
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL

# The application refuses to start without a signing secret. CI provides none,
# so the suite supplies its own unless the environment (the container started
# by setup.sh) already carries one. Set before any application import, since
# the WOPI router reads it at import time.
os.environ.setdefault("SECRET_KEY", "test-suite-signing-secret-not-for-production")

# Application modules read DATABASE_URL at import time, so they are imported
# only after it has been redirected above.
from sqlalchemy import MetaData, create_engine, event, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.automations.automations_models  # noqa: E402,F401 – registers Automation with Base
import app.blocks.models  # noqa: E402,F401 – registers Block, PropertySchema, PropertyValue,
                           #                    BlockPreference, BlockEvent with Base
import app.permissions.model  # noqa: E402,F401 – registers BlockPermission, BlockPermissionGrant
import app.comments.comments_models  # noqa: E402,F401 – registers Comment with Base
import app.media.model  # noqa: E402,F401 – registers MediaFile with Base
import app.database.database as db_module  # noqa: E402
import app.session.deps as deps_module  # noqa: E402
import app.session.session as s  # noqa: E402
import app.session.user_login as user_login_module  # noqa: E402
import app.session.user_registration as user_registration_module  # noqa: E402
import app.setup_router as setup_router_module  # noqa: E402
import app.users.model  # noqa: E402,F401 – registers User with Base
import app.settings.model  # noqa: E402,F401 – registers AppSetting with Base
from app.database.database import normalize_database_url  # noqa: E402

_BACKEND_DIR = Path(__file__).resolve().parents[1]

# Modules that bind ``SessionLocal`` by name at import time. Each is pointed at
# the test engine for every test, so a module reloaded by a test (see
# database_test.py) cannot leave a stale factory behind for the next one.
_SESSION_FACTORY_MODULES = (
    s,
    db_module,
    deps_module,
    user_login_module,
    user_registration_module,
    setup_router_module,
)


def _run_migrations() -> None:
    """
    Apply every Alembic migration to the test database.

    The configuration is built in code rather than read from alembic.ini: the
    ini file installs its own logging configuration, which would disable the
    application loggers that several tests inspect through ``caplog``.
    """
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def test_engine():
    """
    Engine for the test database, with a freshly migrated schema.

    Session-scoped: the schema is rebuilt once per run. The yielded engine is
    owned by this fixture rather than taken from ``app.database.database``,
    because a test that reloads that module replaces its engine.
    """
    engine = create_engine(normalize_database_url(_TEST_DATABASE_URL))
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    _run_migrations()
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def _tables_in_delete_order(test_engine) -> list[str]:
    """
    Every application table, ordered so that referencing tables come first.

    Reflected from the migrated database rather than taken from the models, so
    a table that exists only in a migration is cleared as well.
    """
    metadata = MetaData()
    metadata.reflect(test_engine)
    return [
        table.name
        for table in reversed(metadata.sorted_tables)
        if table.name != "alembic_version"
    ]


@pytest.fixture(autouse=True)
def isolated_db(test_engine, _tables_in_delete_order, monkeypatch):
    """
    Give every test an empty database and route all sessions to it.

    Rows are deleted before the test rather than after, so a test that fails
    half-way leaves its data in place for inspection and still cannot affect
    the next one.
    """
    with test_engine.begin() as connection:
        for table in _tables_in_delete_order:
            connection.execute(text(f'DELETE FROM "{table}"'))

    test_session = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    for module in _SESSION_FACTORY_MODULES:
        monkeypatch.setattr(module, "SessionLocal", test_session)

    yield


class QueryCounter:
    """
    Counts the SQL statements sent to the test database inside a ``with`` block.

    Used by tests that pin how many queries an operation costs, so that an
    operation which starts issuing one query per row is caught regardless of
    how fast the test machine is.
    """

    def __init__(self, engine):
        self._engine = engine
        self.count = 0
        self.statements: list[str] = []

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1
        self.statements.append(statement)

    def __enter__(self) -> "QueryCounter":
        event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc_info) -> None:
        event.remove(self._engine, "before_cursor_execute", self._on_execute)


@pytest.fixture
def count_queries(test_engine):
    """
    Return a factory for :class:`QueryCounter` bound to the test engine.

    Usage::

        with count_queries() as counter:
            ...
        assert counter.count <= 3
    """
    return lambda: QueryCounter(test_engine)


@pytest.fixture
def admin_user():
    """
    The in-memory admin ``http_client`` acts as by default.

    It exists only in memory; tests that need the account as a database row
    create it. Pass it to ``login_as`` to switch back after acting as another
    account.
    """
    import uuid

    from app.users.model import User

    return User(
        id=uuid.uuid4(),
        username="testuser",
        password_hash="x",
        role="admin",
        is_active=True,
        can_print=False,
    )


@pytest.fixture
def _acting_user(admin_user) -> dict:
    """
    Holder for the account ``http_client`` authenticates as.

    Starts with ``admin_user``. ``login_as`` replaces it, which switches the
    identity of every following request without a new client.
    """
    return {"user": admin_user}


@pytest.fixture
def login_as(_acting_user):
    """
    Return a function that makes ``http_client`` act as the given user.

    Admins pass every permission check, so a test that only ever runs as the
    default admin cannot detect a missing check. Tests set up their data as
    the admin, then call ``login_as(member_user)`` and repeat the request to
    prove the endpoint refuses an account without access, and
    ``login_as(admin_user)`` to inspect the state afterwards.
    """
    def _login_as(user) -> None:
        _acting_user["user"] = user

    return _login_as


@pytest.fixture
def member_user():
    """
    Persist and return an active account with the ``member`` role.

    Stored as a database row, unlike the default admin, so code that loads
    accounts from the database (the WebSocket recipient check, user lookups)
    sees it as well.
    """
    import uuid

    from app.users.model import User

    user = User(
        id=uuid.uuid4(),
        username="member-a",
        password_hash="x",
        role="member",
        is_active=True,
    )
    with db_module.SessionLocal() as db:
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
    return user


@pytest.fixture
def http_client(_acting_user):
    """
    Return an authenticated TestClient for endpoint tests.

    Every router, the block router included, consumes ``require_session`` and
    ``get_current_user`` from ``app.session.deps``, so a single pair of FastAPI
    dependency overrides authenticates all of them.

    The ``isolated_db`` fixture (autouse) ensures an empty database for each
    test. By default the client acts as ``admin_user``, so permission checks
    that distinguish members from admins pass without extra setup.
    ``login_as`` switches the client to another account, which is how tests
    reach the refusal paths of those checks.
    """
    from fastapi.testclient import TestClient

    from app.main import app
    from app.session.deps import get_current_user, require_session

    # One override pair covers every router. Both read the holder on every
    # request, so login_as takes effect immediately.
    app.dependency_overrides[require_session] = lambda: _acting_user["user"].id
    app.dependency_overrides[get_current_user] = lambda: _acting_user["user"]

    # Seed the workspace root block that Alembic migration 0002 inserts. The
    # isolation fixture removes it together with every other row, so the
    # HTTP-layer tests that rely on it put it back here; lower-level tests
    # (models, service, repository) seed it themselves.
    from datetime import datetime, timezone

    from app.blocks.models import Block, WORKSPACE_ROOT_ID

    _now = datetime.now(timezone.utc)
    with db_module.SessionLocal() as _seed_db:
        _seed_db.merge(Block(
            id=WORKSPACE_ROOT_ID,
            parent_id=None,
            reference_id=None,
            type="workspace",
            position=0.0,
            state="active",
            content={"title": "Workspace"},
            created_at=_now,
            updated_at=_now,
        ))
        _seed_db.commit()

    try:
        # The cookie is redundant while get_current_user is overridden, but it
        # keeps the client realistic for anything that reads the raw value.
        with TestClient(app, cookies={"session": "stub-token"}) as client:
            yield client
    finally:
        # Cleared even when the test body raises, so a failing test cannot
        # leave every later test authenticated.
        app.dependency_overrides.clear()


@pytest.fixture
def member_client(http_client, login_as, member_user):
    """
    Return ``http_client`` acting as ``member_user`` from the start.

    It is the same client object, so a test that also requests
    ``http_client`` gets the member identity there too; use ``login_as`` to
    switch between accounts within one test.
    """
    login_as(member_user)
    return http_client
