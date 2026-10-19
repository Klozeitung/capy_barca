"""
Tests for the permissions router.

GET  /api/blocks/{block_id}/permissions
PUT  /api/blocks/{block_id}/permissions

HTTP auth is handled via the conftest ``http_client`` fixture, which injects
a fake admin user via dependency override.  Non-admin scenarios either insert
a second user directly into the database and override ``get_current_user``
locally, or switch the client with the ``login_as`` fixture.
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


def _grant(user_id: str, level: str = "write") -> dict:
    return {"user_id": user_id, "level": level}


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
        json={"mode": "whitelist", "grants": [_grant(user_id_1), _grant(user_id_2)]},
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
        json={"mode": "whitelist", "grants": [_grant(old_user)]},
    )
    http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "whitelist", "grants": [_grant(new_user)]},
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
        json={"mode": "whitelist", "grants": [_grant(uid, "read")]},
    )
    assert resp.json()["grants"] == [{"user_id": uid, "level": "read"}]


# ─── Access levels ────────────────────────────────────────────────────────────


def test_grants_default_to_write(http_client):
    page_id = _create_page(http_client)
    uid = str(uuid.uuid4())
    resp = http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "whitelist", "grants": [{"user_id": uid}]},
    )
    assert resp.json()["grants"] == [{"user_id": uid, "level": "write"}]


def test_set_permission_stores_the_level_of_each_grant(http_client):
    page_id = _create_page(http_client)
    reader, writer = str(uuid.uuid4()), str(uuid.uuid4())
    http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "whitelist", "grants": [_grant(reader, "read"), _grant(writer)]},
    )
    with s.SessionLocal() as db:
        grants = (
            db.query(BlockPermissionGrant)
            .filter(BlockPermissionGrant.block_id == uuid.UUID(page_id))
            .all()
        )
    assert {str(g.user_id): g.level for g in grants} == {reader: "read", writer: "write"}


def test_set_permission_changes_the_level_of_a_kept_grant(http_client):
    page_id = _create_page(http_client)
    uid = str(uuid.uuid4())
    url = f"/api/blocks/{page_id}/permissions"
    http_client.put(url, json={"mode": "whitelist", "grants": [_grant(uid)]})
    http_client.put(url, json={"mode": "whitelist", "grants": [_grant(uid, "read")]})
    data = http_client.get(url).json()
    assert data["grants"] == [{"user_id": uid, "level": "read"}]


def test_set_permission_stores_the_level_for_everyone(http_client):
    page_id = _create_page(http_client)
    url = f"/api/blocks/{page_id}/permissions"
    resp = http_client.put(url, json={"mode": "everyone", "everyone_level": "read"})
    assert resp.json()["everyone_level"] == "read"
    assert http_client.get(url).json()["everyone_level"] == "read"


def test_level_for_everyone_defaults_to_write(http_client):
    page_id = _create_page(http_client)
    resp = http_client.put(
        f"/api/blocks/{page_id}/permissions", json={"mode": "everyone"},
    )
    assert resp.json()["everyone_level"] == "write"


def test_inherited_block_reports_write_for_everyone(http_client):
    page_id = _create_page(http_client)
    resp = http_client.put(
        f"/api/blocks/{page_id}/permissions", json={"mode": "inherit"},
    )
    assert resp.json()["everyone_level"] == "write"


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "everyone", "everyone_level": "admin"},
        {"mode": "whitelist", "grants": [{"user_id": str(uuid.uuid4()), "level": "owner"}]},
        {"mode": "whitelist", "grants": [str(uuid.uuid4())]},
    ],
)
def test_set_permission_rejects_an_unknown_level_or_shape(http_client, body):
    page_id = _create_page(http_client)
    resp = http_client.put(f"/api/blocks/{page_id}/permissions", json=body)
    assert resp.status_code == 422


def test_get_permission_is_hidden_from_a_member_without_access(
    http_client, login_as, member_user,
):
    """The grant list names accounts; a caller who cannot see the block gets 404."""
    page_id = _create_page(http_client)
    login_as(member_user)
    resp = http_client.get(f"/api/blocks/{page_id}/permissions")
    assert resp.status_code == 404


def test_get_permission_is_visible_to_a_reader(http_client, login_as, member_user):
    page_id = _create_page(http_client)
    http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "whitelist", "grants": [_grant(str(member_user.id), "read")]},
    )
    login_as(member_user)
    resp = http_client.get(f"/api/blocks/{page_id}/permissions")
    assert resp.status_code == 200
    assert resp.json()["can_edit"] is False


def test_reader_may_not_change_permissions(http_client, login_as, member_user):
    page_id = _create_page(http_client)
    http_client.put(
        f"/api/blocks/{page_id}/permissions",
        json={"mode": "whitelist", "grants": [_grant(str(member_user.id))]},
    )
    login_as(member_user)
    resp = http_client.put(
        f"/api/blocks/{page_id}/permissions", json={"mode": "everyone"},
    )
    assert resp.status_code == 403


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


# ─── Restriction by ancestors ─────────────────────────────────────────────────


def _create_child(http_client, parent_id: str) -> str:
    resp = http_client.post(
        "/api/blocks", json={"type": "page", "parent_id": parent_id},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_wider_row_below_a_private_page_is_flagged(http_client, member_user):
    page_id = _create_page(http_client)
    child_id = _create_child(http_client, page_id)
    resp = http_client.put(
        f"/api/blocks/{child_id}/permissions", json={"mode": "everyone"},
    )
    assert resp.json()["warn_restricted_by_ancestors"] is True
    data = http_client.get(f"/api/blocks/{child_id}/permissions").json()
    assert data["warn_restricted_by_ancestors"] is True


def test_narrowing_row_is_not_flagged(http_client, member_user):
    page_id = _create_page(http_client)
    http_client.put(f"/api/blocks/{page_id}/permissions", json={"mode": "everyone"})
    child_id = _create_child(http_client, page_id)
    resp = http_client.put(
        f"/api/blocks/{child_id}/permissions",
        json={"mode": "whitelist", "grants": [_grant(str(member_user.id), "read")]},
    )
    assert resp.json()["warn_restricted_by_ancestors"] is False


def test_inherit_is_never_flagged(http_client):
    page_id = _create_page(http_client)
    child_id = _create_child(http_client, page_id)
    resp = http_client.put(
        f"/api/blocks/{child_id}/permissions", json={"mode": "inherit"},
    )
    assert resp.json()["warn_restricted_by_ancestors"] is False
