"""
Tests for the automations router.

All tests run against the isolated in-memory SQLite database provided by
the autouse ``isolated_db`` fixture in conftest.py and exercise the HTTP
layer via the ``http_client`` fixture defined there.
"""
import uuid


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
