"""
Tests for the permissions router.

GET  /api/blocks/{block_id}/permissions
PUT  /api/blocks/{block_id}/permissions

HTTP auth is handled via the conftest ``http_client`` fixture, which injects
a fake admin user via dependency override.  Non-admin scenarios are tested
by inserting a second user directly into the database and overriding
``get_current_user`` locally.
"""
import uuid

import pytest

import app.session.session as s
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.permissions.model import BlockPermission, BlockPermissionGrant
from app.users.model import User


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _create_page(http_client) -> str:
    resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_user(db, role: str = "member") -> User:
    user = User(
        id=uuid.uuid4(),
        username=f"user_{uuid.uuid4().hex[:8]}",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ─── GET /api/blocks/{block_id}/permissions ───────────────────────────────────


def test_get_permission_returns_200(http_client):
    page_id = _create_page(http_client)
    resp = http_client.get(f"/api/blocks/{page_id}/permissions")
    assert resp.status_code == 200


def test_get_permission_unknown_block_returns_404(http_client):
    resp = http_client.get(f"/api/blocks/{uuid.uuid4()}/permissions")
    assert resp.status_code == 404


def test_get_permission_first_level_block_defaults_to_private(http_client):
    """Direct children of the workspace root receive mode='private' automatically."""
    page_id = _create_page(http_client)
    data = http_client.get(f"/api/blocks/{page_id}/permissions").json()
    assert data["mode"] == "private"


def test_get_permission_effective_mode_is_private_for_first_level_block(http_client):
    """First-level blocks carry an explicit 'private' row, so effective_mode is 'private'."""
    page_id = _create_page(http_client)
    data = http_client.get(f"/api/blocks/{page_id}/permissions").json()
    assert data["effective_mode"] == "private"


def test_get_permission_grants_empty_by_default(http_client):
    page_id = _create_page(http_client)
    data = http_client.get(f"/api/blocks/{page_id}/permissions").json()
    assert data["grants"] == []


def test_get_permission_returns_owner_id(http_client):
    page_id = _create_page(http_client)
    data = http_client.get(f"/api/blocks/{page_id}/permissions").json()
    # owner_id may be None in isolated test DB (no backfill migration runs),
    # but the field must be present.
    assert "owner_id" in data


# ─── PUT /api/blocks/{block_id}/permissions ───────────────────────────────────


def test_set_permission_returns_200(http_client):
    page_id = _create_page(http_client)
    resp = http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "everyone"},
    )
    assert resp.status_code == 200


def test_set_permission_unknown_block_returns_404(http_client):
    resp = http_client.put(
        f"/api/blocks/{uuid.uuid4()}/permissions",
        json={"mode": "everyone"},
    )
    assert resp.status_code == 404


def test_set_permission_invalid_mode_returns_422(http_client):
    page_id = _create_page(http_client)
    resp = http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "superuser"},
    )
    assert resp.status_code == 422


def test_set_permission_everyone_stores_row(http_client):
    page_id = _create_page(http_client)
    http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "everyone"},
    )
    with s.SessionLocal() as db:
        row = db.get(BlockPermission, uuid.UUID(page_id))
    assert row is not None
    assert row.mode == "everyone"


def test_set_permission_private_stores_row(http_client):
    page_id = _create_page(http_client)
    http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "private"},
    )
    with s.SessionLocal() as db:
        row = db.get(BlockPermission, uuid.UUID(page_id))
    assert row is not None
    assert row.mode == "private"


def test_set_permission_inherit_removes_row(http_client):
    page_id = _create_page(http_client)
    # First set an explicit mode
    http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "everyone"},
    )
    # Then revert to inherit
    http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "inherit"},
    )
    with s.SessionLocal() as db:
        row = db.get(BlockPermission, uuid.UUID(page_id))
    assert row is None


def test_set_permission_inherit_returns_inherit_mode(http_client):
    page_id = _create_page(http_client)
    resp = http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "inherit"},
    )
    assert resp.json()["mode"] == "inherit"


def test_set_permission_whitelist_stores_grants(http_client):
    page_id = _create_page(http_client)
    user_id_1 = str(uuid.uuid4())
    user_id_2 = str(uuid.uuid4())
    http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "whitelist", "grants": [user_id_1, user_id_2]},
    )
    with s.SessionLocal() as db:
        grants = (
            db.query(BlockPermissionGrant)
            .filter(BlockPermissionGrant.block_id == uuid.UUID(page_id))
            .all()
        )
    stored_ids = {str(g.user_id) for g in grants}
    assert user_id_1 in stored_ids
    assert user_id_2 in stored_ids


def test_set_permission_replaces_existing_grants(http_client):
    page_id = _create_page(http_client)
    old_user = str(uuid.uuid4())
    new_user = str(uuid.uuid4())

    http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "whitelist", "grants": [old_user]},
    )
    http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "whitelist", "grants": [new_user]},
    )
    with s.SessionLocal() as db:
        grants = (
            db.query(BlockPermissionGrant)
            .filter(BlockPermissionGrant.block_id == uuid.UUID(page_id))
            .all()
        )
    stored_ids = {str(g.user_id) for g in grants}
    assert new_user in stored_ids
    assert old_user not in stored_ids


def test_set_permission_response_contains_grants(http_client):
    page_id = _create_page(http_client)
    uid = str(uuid.uuid4())
    resp = http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "whitelist", "grants": [uid]},
    )
    assert uid in [str(g) for g in resp.json()["grants"]]


def test_set_permission_non_owner_member_returns_403(http_client):
    """A member who is not the owner receives 403.

    The page is created first (while the admin dep override from the
    http_client fixture is active), ensuring the block has no owner set
    in the test session.  A member then attempts to change permissions and
    must receive 403 because owner_id is None and the member is not an admin.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.session.deps import get_current_user

    # 1. Create the page while admin is the active user.
    page_id = _create_page(http_client)

    # 2. Create a non-admin member and override the dep to that member.
    with s.SessionLocal() as db:
        member = _create_user(db, role="member")

    app.dependency_overrides[get_current_user] = lambda: member
    try:
        client = TestClient(app, cookies={"session": "stub-token"})
        resp = client.put(
            f"/api/blocks/{page_id}/permissions",
            json={"mode": "everyone"},
        )
        assert resp.status_code == 403
    finally:
        # Remove the member override; the http_client fixture teardown
        # calls app.dependency_overrides.clear() automatically.
        app.dependency_overrides.pop(get_current_user, None)


def test_permissions_route_is_registered():
    """Verify the route exists; unauthenticated access must return 401."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.get(f"/api/blocks/{uuid.uuid4()}/permissions")
    assert resp.status_code == 401
