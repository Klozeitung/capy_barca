"""
Tests for the automations router.

All tests run against the isolated in-memory SQLite database provided by
the autouse ``isolated_db`` fixture in conftest.py and exercise the HTTP
layer via the ``http_client`` fixture defined there.

That fixture authenticates as an admin, who bypasses the permission layer, so
the CRUD tests below exercise endpoint behaviour rather than authorization.
The authorization section at the end builds its own member clients and seeds
real database blocks — the ``database_id`` values used by the CRUD tests are
random UUIDs that point at no block at all, which is fine for an admin and
useless for testing a permission rule.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import app.session.session as s
from app.automations import automations_repository as auto_repo
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.main import app
from app.permissions import repository as perm_repo
from app.session.deps import get_current_user
from app.users.model import User


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _sample_trigger(db_uuid: str = "", property_uuid: str = "") -> dict:
    return {
        "action_type":   "PropertyUpdate",
        "origin":        "user",
        "actor_uuid":    "",
        "db_uuid":       db_uuid,
        "property_uuid": property_uuid,
        "old_value":     "",
        "new_value":     "",
    }


def _create_automation(
    http_client,
    *,
    database_id: str | None = None,
    name: str = "My Automation",
    trigger: dict | None = None,
    actions: list | None = None,
    enabled: bool = True,
) -> dict:
    db_id = database_id or str(uuid.uuid4())
    body = {
        "database_id": db_id,
        "name": name,
        "trigger": trigger or _sample_trigger(db_uuid=db_id),
        "actions": actions or [],
        "enabled": enabled,
    }
    resp = http_client.post("/api/automations", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ─── GET /api/automations ─────────────────────────────────────────────────────


def test_list_automations_empty(http_client):
    resp = http_client.get("/api/automations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_automations_returns_created(http_client):
    _create_automation(http_client, name="First")
    _create_automation(http_client, name="Second")
    result = http_client.get("/api/automations").json()
    assert len(result) == 2
    names = {a["name"] for a in result}
    assert names == {"First", "Second"}


def test_list_automations_filter_by_database_id(http_client):
    db_a = str(uuid.uuid4())
    db_b = str(uuid.uuid4())
    _create_automation(http_client, database_id=db_a, name="For A")
    _create_automation(http_client, database_id=db_b, name="For B")

    result = http_client.get(f"/api/automations?database_id={db_a}").json()
    assert len(result) == 1
    assert result[0]["name"] == "For A"


# ─── GET /api/automations/{id} ────────────────────────────────────────────────


def test_get_automation(http_client):
    created = _create_automation(http_client, name="Fetch Me")
    resp = http_client.get(f"/api/automations/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Fetch Me"


def test_get_automation_not_found(http_client):
    resp = http_client.get(f"/api/automations/{uuid.uuid4()}")
    assert resp.status_code == 404


# ─── POST /api/automations ───────────────────────────────────────────────────


def test_create_automation_returns_201(http_client):
    db_id = str(uuid.uuid4())
    resp = http_client.post("/api/automations", json={
        "database_id": db_id,
        "name": "New Auto",
        "trigger": _sample_trigger(db_uuid=db_id),
        "actions": [],
    })
    assert resp.status_code == 201


def test_create_automation_response_fields(http_client):
    db_id = str(uuid.uuid4())
    trigger = _sample_trigger(db_uuid=db_id)
    actions = [{"endpoint": "PUT /api/foo", "body": {}}]
    resp = http_client.post("/api/automations", json={
        "database_id": db_id,
        "name": "Full",
        "trigger": trigger,
        "actions": actions,
        "enabled": False,
    })
    data = resp.json()
    assert data["name"] == "Full"
    assert data["enabled"] is False
    assert data["trigger"] == trigger
    assert data["actions"] == actions
    assert uuid.UUID(data["id"])
    assert uuid.UUID(data["database_id"])


def test_create_automation_enabled_defaults_to_true(http_client):
    auto = _create_automation(http_client)
    assert auto["enabled"] is True


# ─── PATCH /api/automations/{id} ─────────────────────────────────────────────


def test_update_automation_name(http_client):
    auto = _create_automation(http_client, name="Old Name")
    resp = http_client.patch(
        f"/api/automations/{auto['id']}", json={"name": "New Name"}
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


def test_update_automation_enabled(http_client):
    auto = _create_automation(http_client, enabled=True)
    resp = http_client.patch(
        f"/api/automations/{auto['id']}", json={"enabled": False}
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_update_automation_actions(http_client):
    auto = _create_automation(http_client, actions=[])
    new_actions = [{"endpoint": "PUT /api/some/path", "body": {"value": None}}]
    resp = http_client.patch(
        f"/api/automations/{auto['id']}", json={"actions": new_actions}
    )
    assert resp.status_code == 200
    assert resp.json()["actions"] == new_actions


def test_update_automation_not_found(http_client):
    resp = http_client.patch(
        f"/api/automations/{uuid.uuid4()}", json={"name": "X"}
    )
    assert resp.status_code == 404


def test_update_automation_partial_leaves_other_fields(http_client):
    db_id = str(uuid.uuid4())
    trigger = _sample_trigger(db_uuid=db_id)
    auto = _create_automation(http_client, database_id=db_id, trigger=trigger)

    http_client.patch(f"/api/automations/{auto['id']}", json={"name": "Renamed"})
    fetched = http_client.get(f"/api/automations/{auto['id']}").json()
    assert fetched["trigger"] == trigger  # unchanged


# ─── DELETE /api/automations/{id} ────────────────────────────────────────────


def test_delete_automation(http_client):
    auto = _create_automation(http_client)
    resp = http_client.delete(f"/api/automations/{auto['id']}")
    assert resp.status_code == 204

    # Confirm it is gone
    assert http_client.get(f"/api/automations/{auto['id']}").status_code == 404


def test_delete_automation_not_found(http_client):
    resp = http_client.delete(f"/api/automations/{uuid.uuid4()}")
    assert resp.status_code == 404


# ─── PATCH /api/automations/{id}/toggle ──────────────────────────────────────


def test_toggle_automation_enabled_to_disabled(http_client):
    auto = _create_automation(http_client, enabled=True)
    resp = http_client.patch(f"/api/automations/{auto['id']}/toggle")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_toggle_automation_disabled_to_enabled(http_client):
    auto = _create_automation(http_client, enabled=False)
    resp = http_client.patch(f"/api/automations/{auto['id']}/toggle")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


def test_toggle_automation_twice_returns_to_original(http_client):
    auto = _create_automation(http_client, enabled=True)
    aid = auto["id"]
    http_client.patch(f"/api/automations/{aid}/toggle")
    resp = http_client.patch(f"/api/automations/{aid}/toggle")
    assert resp.json()["enabled"] is True


def test_toggle_automation_not_found(http_client):
    resp = http_client.patch(f"/api/automations/{uuid.uuid4()}/toggle")
    assert resp.status_code == 404


# ─── Auth guard ───────────────────────────────────────────────────────────────


def test_unauthenticated_list_returns_401():
    """Without the http_client auth overrides, the endpoint must reject the request."""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        resp = c.get("/api/automations")
    assert resp.status_code == 401


# ─── Authorization helpers ────────────────────────────────────────────────────


def _make_user(role: str = "member") -> User:
    user = User(
        id=uuid.uuid4(),
        username=f"user_{uuid.uuid4().hex[:8]}",
        password_hash="x",
        role=role,
        is_active=True,
    )
    with s.SessionLocal() as db:
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
    return user


def _seed_database_block(owner_id=None, mode: str = "private", grants=()) -> uuid.UUID:
    """Create a database block with an explicit permission row."""
    block_id = uuid.uuid4()
    with s.SessionLocal() as db:
        db.merge(Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0, state="active"))
        block = Block(
            id=block_id,
            parent_id=WORKSPACE_ROOT_ID,
            type="database",
            position=1.0,
            state="active",
        )
        block.owner_id = owner_id
        db.add(block)
        db.flush()
        perm_repo.set_permission(db, block_id, mode, list(grants))
        db.commit()
    return block_id


def _seed_automation(database_id: uuid.UUID, name: str = "Seeded") -> str:
    with s.SessionLocal() as db:
        automation = auto_repo.create_automation(
            db,
            database_id=database_id,
            name=name,
            trigger=_sample_trigger(db_uuid=str(database_id)),
            actions=[],
            enabled=True,
        )
        db.commit()
        db.refresh(automation)
        automation_id = automation.id
    return str(automation_id)


def _automation_exists(automation_id: str) -> bool:
    with s.SessionLocal() as db:
        return auto_repo.get_automation(db, uuid.UUID(automation_id)) is not None


@pytest.fixture
def client_factory(isolated_db):
    """Build a TestClient authenticated as a specific account."""
    def _make(user: User) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: user
        client = TestClient(app)
        client.cookies.set("session", "test-token")
        return client

    yield _make
    app.dependency_overrides.clear()


# ─── Listing is filtered, not refused ─────────────────────────────────────────


def test_list_filters_to_reachable_databases(client_factory):
    member = _make_user()
    mine = _seed_database_block(owner_id=member.id, mode="private")
    theirs = _seed_database_block(owner_id=uuid.uuid4(), mode="private")
    _seed_automation(mine, name="Mine")
    _seed_automation(theirs, name="Theirs")

    client = client_factory(member)
    result = client.get("/api/automations").json()
    assert [a["name"] for a in result] == ["Mine"]


def test_list_with_unreachable_database_id_returns_403(client_factory):
    member = _make_user()
    theirs = _seed_database_block(owner_id=uuid.uuid4(), mode="private")
    _seed_automation(theirs)
    client = client_factory(member)
    assert client.get(f"/api/automations?database_id={theirs}").status_code == 403


def test_list_with_own_database_id_returns_it(client_factory):
    member = _make_user()
    mine = _seed_database_block(owner_id=member.id, mode="private")
    _seed_automation(mine, name="Mine")
    client = client_factory(member)
    result = client.get(f"/api/automations?database_id={mine}").json()
    assert [a["name"] for a in result] == ["Mine"]


def test_admin_lists_automations_of_every_database(client_factory):
    admin = _make_user(role="admin")
    _seed_automation(_seed_database_block(owner_id=uuid.uuid4(), mode="private"))
    _seed_automation(_seed_database_block(owner_id=uuid.uuid4(), mode="private"))
    client = client_factory(admin)
    assert len(client.get("/api/automations").json()) == 2


# ─── Single-item endpoints ────────────────────────────────────────────────────


def test_create_on_unreachable_database_returns_403(client_factory):
    member = _make_user()
    theirs = _seed_database_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member)
    r = client.post("/api/automations", json={
        "database_id": str(theirs),
        "name": "Injected",
        "trigger": _sample_trigger(db_uuid=str(theirs)),
        "actions": [],
    })
    assert r.status_code == 403


def test_create_on_own_database_is_allowed(client_factory):
    member = _make_user()
    mine = _seed_database_block(owner_id=member.id, mode="private")
    client = client_factory(member)
    r = client.post("/api/automations", json={
        "database_id": str(mine),
        "name": "Mine",
        "trigger": _sample_trigger(db_uuid=str(mine)),
        "actions": [],
    })
    assert r.status_code == 201


def test_get_on_unreachable_database_returns_403(client_factory):
    member = _make_user()
    theirs = _seed_database_block(owner_id=uuid.uuid4(), mode="private")
    automation_id = _seed_automation(theirs)
    client = client_factory(member)
    assert client.get(f"/api/automations/{automation_id}").status_code == 403


def test_update_on_unreachable_database_returns_403(client_factory):
    member = _make_user()
    theirs = _seed_database_block(owner_id=uuid.uuid4(), mode="private")
    automation_id = _seed_automation(theirs, name="Untouched")
    client = client_factory(member)
    r = client.patch(f"/api/automations/{automation_id}", json={"name": "Hijacked"})
    assert r.status_code == 403


def test_delete_on_unreachable_database_returns_403(client_factory):
    member = _make_user()
    theirs = _seed_database_block(owner_id=uuid.uuid4(), mode="private")
    automation_id = _seed_automation(theirs)
    client = client_factory(member)
    assert client.delete(f"/api/automations/{automation_id}").status_code == 403
    assert _automation_exists(automation_id)


def test_toggle_on_unreachable_database_returns_403(client_factory):
    member = _make_user()
    theirs = _seed_database_block(owner_id=uuid.uuid4(), mode="private")
    automation_id = _seed_automation(theirs)
    client = client_factory(member)
    assert client.patch(
        f"/api/automations/{automation_id}/toggle"
    ).status_code == 403


def test_unknown_automation_still_returns_404(client_factory):
    """The guard must not turn a genuine 404 into a permission error."""
    member = _make_user()
    client = client_factory(member)
    assert client.get(f"/api/automations/{uuid.uuid4()}").status_code == 404


def test_member_may_manage_automations_on_a_granted_database(client_factory):
    member = _make_user()
    shared = _seed_database_block(
        owner_id=uuid.uuid4(), mode="whitelist", grants=[member.id]
    )
    automation_id = _seed_automation(shared, name="Shared")
    client = client_factory(member)
    assert client.get(f"/api/automations/{automation_id}").status_code == 200
    assert client.patch(
        f"/api/automations/{automation_id}", json={"name": "Renamed"}
    ).status_code == 200
    assert client.patch(
        f"/api/automations/{automation_id}/toggle"
    ).status_code == 200
    assert client.delete(f"/api/automations/{automation_id}").status_code == 204
