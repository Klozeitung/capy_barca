import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.automations.automations_models  # noqa: F401 – registers Automation with Base
import app.blocks.models  # noqa: F401 – registers Block, PropertySchema, PropertyValue,
                           #               BlockPreference, BlockEvent with Base
import app.permissions.model  # noqa: F401 – registers BlockPermission, BlockPermissionGrant with Base
import app.comments.comments_models  # noqa: F401 – registers Comment with Base
import app.media.model  # noqa: F401 – registers MediaFile with Base
import app.comments.comments_router as comments_router_module
import app.database.database as db_module
import app.session.deps as deps_module
import app.session.session as s
import app.session.user_login as user_login_module
import app.session.user_registration as user_registration_module
import app.setup_router as setup_router_module
import app.users.model  # noqa: F401 – registers User with Base
from app.session.session import SessionRecord


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """
    Replace every live database session factory with an in-memory SQLite
    instance for the duration of each test.

    Patched references
    ------------------
    1. ``app.session.session.SessionLocal``        – session/auth token logic
    2. ``app.database.database.SessionLocal``      – canonical source for late importers
    3. ``app.session.deps.SessionLocal``           – ``get_db`` in the shared deps module.
                                                     Every router now reaches the database
                                                     through this one, the block router
                                                     included: it re-exports ``get_db``
                                                     from deps rather than defining its own.
    4. ``app.session.user_login.SessionLocal``     – verifyLogin DB session
    5. ``app.session.user_registration.SessionLocal`` – create_admin DB session
    6. ``app.setup_router.SessionLocal``           – _is_configured DB session

    StaticPool ensures all connections within a test share the same in-memory
    database, so tables created by ``create_all`` are visible to every
    subsequent ``SessionLocal()`` call.

    Autouse ensures every test in the suite benefits from full isolation
    without any per-test boilerplate.
    """
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    TestSession = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    SessionRecord.metadata.create_all(test_engine)

    monkeypatch.setattr(s, "SessionLocal", TestSession)
    monkeypatch.setattr(db_module, "SessionLocal", TestSession)
    monkeypatch.setattr(deps_module, "SessionLocal", TestSession)
    monkeypatch.setattr(user_login_module, "SessionLocal", TestSession)
    monkeypatch.setattr(user_registration_module, "SessionLocal", TestSession)
    monkeypatch.setattr(setup_router_module, "SessionLocal", TestSession)

    yield

    SessionRecord.metadata.drop_all(test_engine)
    test_engine.dispose()


@pytest.fixture
def http_client():
    """
    Return an authenticated TestClient for endpoint tests.

    Every router, the block router included, consumes ``require_session`` and
    ``get_current_user`` from ``app.session.deps``, so a single pair of FastAPI
    dependency overrides authenticates all of them. The block router used to
    need a second mechanism — a monkeypatched ``app.blocks.router.validate_token``
    for its own module-local ``require_session`` — which is gone along with the
    duplicate auth path it existed for.

    The ``isolated_db`` fixture (autouse) ensures a clean in-memory database
    for each test.  The fake user carries the admin role so permission checks
    that distinguish members from admins pass without extra setup.
    """
    import uuid

    from fastapi.testclient import TestClient

    from app.main import app
    from app.session.deps import get_current_user, require_session
    from app.users.model import User

    fake_user = User(
        id=uuid.uuid4(),
        username="testuser",
        password_hash="x",
        role="admin",
        is_active=True,
    )

    # One override pair covers every router.
    app.dependency_overrides[require_session] = lambda: "stub-session"
    app.dependency_overrides[get_current_user] = lambda: fake_user

    # Seed the workspace root block that Alembic migration 0002 normally inserts.
    # Only HTTP-layer tests (those that use http_client) need this row to exist;
    # lower-level tests (models, service, repository) seed it themselves.
    from app.blocks.models import Block, WORKSPACE_ROOT_ID
    from datetime import datetime, timezone
    import app.database.database as _db_module_inner
    _now = datetime.now(timezone.utc)
    with _db_module_inner.SessionLocal() as _seed_db:
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

    # The cookie is redundant while get_current_user is overridden, but it keeps
    # the client realistic for anything that reads the raw session value.
    with TestClient(app, cookies={"session": "stub-token"}) as client:
        yield client

    app.dependency_overrides.clear()
