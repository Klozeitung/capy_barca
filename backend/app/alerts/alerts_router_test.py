"""
Tests for the alerts router and for how alerts reach the entry endpoints.

They exercise the HTTP layer through ``http_client`` (conftest.py). The
default admin exists only in memory there; alerts reference their account
with a foreign key, so the ``acting_admin`` fixture stores it.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.database.database as db_module
from app.alerts.alerts_models import MAX_ALERTS_PER_ENTRY, MAX_TITLE_LENGTH, Alert
from app.blocks.models import WORKSPACE_ROOT_ID, PropertyValue


# ─── Fixtures and helpers ─────────────────────────────────────────────────────


@pytest.fixture
def acting_admin(http_client, admin_user):
    """Persist the admin ``http_client`` acts as."""
    with db_module.SessionLocal() as db:
        db.merge(admin_user)
        db.commit()
    return admin_user


def _future(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _database(http_client) -> str:
    resp = http_client.post(
        "/api/blocks", json={"type": "database", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _schema(http_client, database_id: str, name: str = "Reminders", type_: str = "alerts"):
    return http_client.post(
        f"/api/databases/{database_id}/schemas", json={"name": name, "type": type_},
    )


def _entry(http_client, database_id: str) -> str:
    resp = http_client.post(f"/api/databases/{database_id}/entries")
    assert resp.status_code == 201
    return resp.json()["id"]


def _share(http_client, database_id: str) -> None:
    """Let every account write in the database; new top-level blocks start private."""
    resp = http_client.put(
        f"/api/blocks/{database_id}/permissions", json={"mode": "everyone", "grants": []},
    )
    assert resp.status_code == 200


def _setup(http_client) -> tuple[str, str, str]:
    database_id = _database(http_client)
    schema = _schema(http_client, database_id)
    assert schema.status_code == 201
    return database_id, schema.json()["id"], _entry(http_client, database_id)


def _create(http_client, entry_id: str, *, at: str | None = None, title: str = ""):
    return http_client.post(
        "/api/alerts", json={"block_id": entry_id, "at": at or _future(), "title": title},
    )


def _entries(http_client, database_id: str) -> dict:
    resp = http_client.get(f"/api/databases/{database_id}/entries")
    assert resp.status_code == 200
    return {e["id"]: e for e in resp.json()}


# ─── Create ───────────────────────────────────────────────────────────────────


def test_create_stores_the_alert_for_the_caller(http_client, acting_admin):
    _, schema_id, entry_id = _setup(http_client)

    resp = _create(http_client, entry_id, title="  Title A  ")

    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Title A"
    assert body["schema_id"] == schema_id
    assert body["at"].endswith("Z")
    with db_module.SessionLocal() as db:
        alert = db.get(Alert, uuid.UUID(body["id"]))
        assert alert.user_id == acting_admin.id


def test_create_converts_any_offset_to_utc(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    local = (datetime.now(timezone(timedelta(hours=2))) + timedelta(days=1)).replace(microsecond=0)

    body = _create(http_client, entry_id, at=local.isoformat()).json()

    assert body["at"] == local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_create_refuses_a_time_in_the_past(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert _create(http_client, entry_id, at=past).status_code == 422


def test_create_refuses_a_time_without_zone(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    naive = (datetime.now() + timedelta(days=1)).replace(tzinfo=None).isoformat()
    assert _create(http_client, entry_id, at=naive).status_code == 422


def test_create_refuses_a_title_that_is_too_long(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    resp = _create(http_client, entry_id, title="x" * (MAX_TITLE_LENGTH + 1))
    assert resp.status_code == 422


def test_create_refuses_more_than_the_limit_per_entry(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    for _ in range(MAX_ALERTS_PER_ENTRY):
        assert _create(http_client, entry_id).status_code == 201
    assert _create(http_client, entry_id).status_code == 422


def test_create_refuses_a_database_without_alerts_property(http_client, acting_admin):
    database_id = _database(http_client)
    entry_id = _entry(http_client, database_id)
    assert _create(http_client, entry_id).status_code == 422


def test_create_refuses_a_block_that_is_no_entry(http_client, acting_admin):
    resp = http_client.post(
        "/api/blocks", json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    assert _create(http_client, resp.json()["id"]).status_code == 422


def test_create_on_an_unknown_entry_answers_404(http_client, acting_admin):
    assert _create(http_client, str(uuid.uuid4())).status_code == 404


def test_read_access_is_enough_to_set_an_alert(http_client, login_as, member_user, acting_admin):
    database_id, _, entry_id = _setup(http_client)
    resp = http_client.put(
        f"/api/blocks/{database_id}/permissions",
        json={"mode": "whitelist", "grants": [{"user_id": str(member_user.id), "level": "read"}]},
    )
    assert resp.status_code == 200

    login_as(member_user)
    assert _create(http_client, entry_id).status_code == 201


def test_no_access_refuses_the_alert(http_client, login_as, member_user, acting_admin):
    database_id, _, entry_id = _setup(http_client)
    http_client.put(
        f"/api/blocks/{database_id}/permissions", json={"mode": "private", "grants": []},
    )

    login_as(member_user)
    assert _create(http_client, entry_id).status_code == 403


# ─── Own alerts only ──────────────────────────────────────────────────────────


def test_another_accounts_alert_answers_404_everywhere(
    http_client, login_as, member_user, acting_admin,
):
    _, _, entry_id = _setup(http_client)
    alert_id = _create(http_client, entry_id).json()["id"]

    login_as(member_user)
    assert http_client.get(f"/api/alerts/{alert_id}").status_code == 404
    assert http_client.patch(f"/api/alerts/{alert_id}", json={"title": "x"}).status_code == 404
    assert http_client.post(f"/api/alerts/{alert_id}/dismiss").status_code == 404
    assert http_client.delete(f"/api/alerts/{alert_id}").status_code == 404

    login_as(acting_admin)
    assert http_client.get(f"/api/alerts/{alert_id}").status_code == 200


def test_entry_listings_carry_only_the_callers_alerts(
    http_client, login_as, member_user, acting_admin,
):
    database_id, schema_id, entry_id = _setup(http_client)
    _share(http_client, database_id)
    _create(http_client, entry_id, title="Admin alert")
    login_as(member_user)
    _create(http_client, entry_id, title="Member alert")

    member_cell = _entries(http_client, database_id)[entry_id]["values"][schema_id]
    login_as(acting_admin)
    admin_cell = _entries(http_client, database_id)[entry_id]["values"][schema_id]

    assert [a["title"] for a in member_cell["alerts"]] == ["Member alert"]
    assert [a["title"] for a in admin_cell["alerts"]] == ["Admin alert"]


def test_every_entry_endpoint_carries_the_callers_alerts(http_client, acting_admin):
    database_id, schema_id, entry_id = _setup(http_client)
    _create(http_client, entry_id, title="Title A")

    def titles(values: dict) -> list[str]:
        return [a["title"] for a in values[schema_id]["alerts"]]

    query = http_client.post(f"/api/databases/{database_id}/entries/query", json={}).json()
    detail = http_client.get(f"/api/databases/{database_id}/entries/{entry_id}").json()
    batch = http_client.post(
        f"/api/databases/{database_id}/entries/batch", json={"ids": [entry_id]},
    ).json()
    view_id = http_client.get(f"/api/databases/{database_id}/views").json()["views"][0]["id"]
    page = http_client.post(
        f"/api/databases/{database_id}/views/{view_id}/entries", json={},
    ).json()

    assert titles(query["entries"][0]["values"]) == ["Title A"]
    assert titles(detail["entry"]["values"]) == ["Title A"]
    assert titles(batch["entries"][0]["values"]) == ["Title A"]
    assert titles(page["entries"][0]["values"]) == ["Title A"]


def test_the_generic_value_endpoint_refuses_an_alerts_property(http_client, acting_admin):
    database_id, schema_id, entry_id = _setup(http_client)
    resp = http_client.put(
        f"/api/databases/{database_id}/entries/{entry_id}/values/{schema_id}",
        json={"value": {"alerts": []}},
    )
    assert resp.status_code == 422
    with db_module.SessionLocal() as db:
        assert db.query(PropertyValue).filter_by(property_schema_id=uuid.UUID(schema_id)).count() == 0


# ─── Update, delete, dismiss ──────────────────────────────────────────────────


def test_update_changes_time_and_title(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    alert_id = _create(http_client, entry_id, title="Title A").json()["id"]
    new_at = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)

    resp = http_client.patch(
        f"/api/alerts/{alert_id}", json={"at": new_at.isoformat(), "title": "Title B"},
    )

    assert resp.status_code == 200
    assert resp.json()["title"] == "Title B"
    assert resp.json()["at"] == new_at.isoformat().replace("+00:00", "Z")


def test_update_refuses_a_time_in_the_past(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    alert_id = _create(http_client, entry_id).json()["id"]
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert http_client.patch(f"/api/alerts/{alert_id}", json={"at": past}).status_code == 422


def test_delete_removes_the_alert(http_client, acting_admin):
    database_id, schema_id, entry_id = _setup(http_client)
    alert_id = _create(http_client, entry_id).json()["id"]

    assert http_client.delete(f"/api/alerts/{alert_id}").status_code == 204
    assert http_client.get(f"/api/alerts/{alert_id}").status_code == 404
    assert _entries(http_client, database_id)[entry_id]["values"][schema_id] == {"alerts": []}


def _fire(alert_id: str) -> None:
    with db_module.SessionLocal() as db:
        alert = db.get(Alert, uuid.UUID(alert_id))
        alert.fired_at = datetime.now(timezone.utc)
        alert.delivered = True
        db.commit()


def test_pending_lists_delivered_alerts_until_dismissed(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    alert_id = _create(http_client, entry_id, title="Title A").json()["id"]
    _fire(alert_id)

    pending = http_client.get("/api/alerts/pending").json()
    assert [(p["id"], p["title"]) for p in pending] == [(alert_id, "Title A")]
    assert "block_title" in pending[0]

    resp = http_client.post(f"/api/alerts/{alert_id}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["dismissed_at"] is not None
    assert http_client.get("/api/alerts/pending").json() == []


def test_pending_hides_alerts_on_entries_the_caller_can_no_longer_read(
    http_client, login_as, member_user, acting_admin,
):
    database_id, _, entry_id = _setup(http_client)
    _share(http_client, database_id)
    login_as(member_user)
    alert_id = _create(http_client, entry_id).json()["id"]
    _fire(alert_id)
    assert len(http_client.get("/api/alerts/pending").json()) == 1

    login_as(acting_admin)
    http_client.put(
        f"/api/blocks/{database_id}/permissions", json={"mode": "private", "grants": []},
    )
    login_as(member_user)
    assert http_client.get("/api/alerts/pending").json() == []


def test_upcoming_lists_unfired_alerts_earliest_first(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    late = _create(http_client, entry_id, at=_future(5)).json()["id"]
    soon = _create(http_client, entry_id, at=_future(2)).json()["id"]
    fired = _create(http_client, entry_id, at=_future(1)).json()["id"]
    _fire(fired)

    assert [a["id"] for a in http_client.get("/api/alerts/upcoming").json()] == [soon, late]


# ─── Live messages ────────────────────────────────────────────────────────────


class _Socket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def accept(self) -> None:
        pass

    async def send_text(self, message: str) -> None:
        self.sent.append(message)

    async def close(self, code: int = 1000) -> None:
        pass


def test_changes_and_dismissals_reach_the_callers_connections(http_client, acting_admin):
    from app.ws.manager import manager

    database_id, schema_id, entry_id = _setup(http_client)
    socket = _Socket()
    http_client.portal.call(manager.connect, socket, acting_admin.id)
    try:
        alert_id = _create(http_client, entry_id, title="Title A").json()["id"]
        _fire(alert_id)
        http_client.post(f"/api/alerts/{alert_id}/dismiss")
        http_client.portal.call(manager.flush)
    finally:
        http_client.portal.call(asyncio.sleep, 0)
        manager._connections.clear()

    messages = [json.loads(m) for m in socket.sent]
    changed = next(m for m in messages if m["type"] == "alert.changed")
    assert changed["payload"]["block_id"] == entry_id
    assert changed["payload"]["schema_id"] == schema_id
    assert changed["payload"]["database_id"] == database_id
    assert [a["title"] for a in changed["payload"]["value"]["alerts"]] == ["Title A"]
    assert {"type": "alert.dismissed", "payload": {"id": alert_id}} in messages


# ─── Property lifecycle ───────────────────────────────────────────────────────


def test_a_database_has_at_most_one_alerts_property(http_client, acting_admin):
    database_id, _, _ = _setup(http_client)
    assert _schema(http_client, database_id, "Second").status_code == 409

    other = _schema(http_client, database_id, "Notes", "text").json()
    resp = http_client.patch(
        f"/api/databases/{database_id}/schemas/{other['id']}", json={"type": "alerts"},
    )
    assert resp.status_code == 409


def test_retyping_out_of_alerts_deletes_its_alerts(http_client, acting_admin):
    database_id, schema_id, entry_id = _setup(http_client)
    _create(http_client, entry_id)

    resp = http_client.patch(
        f"/api/databases/{database_id}/schemas/{schema_id}", json={"type": "text"},
    )

    assert resp.status_code == 200
    with db_module.SessionLocal() as db:
        assert db.query(Alert).count() == 0


def test_retyping_into_alerts_drops_the_stored_values(http_client, acting_admin):
    database_id = _database(http_client)
    entry_id = _entry(http_client, database_id)
    schema_id = _schema(http_client, database_id, "Notes", "text").json()["id"]
    http_client.put(
        f"/api/databases/{database_id}/entries/{entry_id}/values/{schema_id}",
        json={"value": {"text": "Text A"}},
    )

    resp = http_client.patch(
        f"/api/databases/{database_id}/schemas/{schema_id}", json={"type": "alerts"},
    )

    assert resp.status_code == 200
    assert _entries(http_client, database_id)[entry_id]["values"][schema_id] == {"alerts": []}


def test_deleting_the_property_deletes_its_alerts(http_client, acting_admin):
    database_id, schema_id, entry_id = _setup(http_client)
    _create(http_client, entry_id)

    assert http_client.delete(f"/api/databases/{database_id}/schemas/{schema_id}").status_code == 204
    with db_module.SessionLocal() as db:
        assert db.query(Alert).count() == 0


def test_moving_the_entry_takes_its_alerts_to_the_target_property(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    alert_id = _create(http_client, entry_id).json()["id"]
    target_id = _database(http_client)
    target_schema = _schema(http_client, target_id, "Other name").json()["id"]

    resp = http_client.post(
        f"/api/blocks/{entry_id}/move", json={"new_parent_id": target_id, "new_position": 1.0},
    )

    assert resp.status_code == 200
    assert http_client.get(f"/api/alerts/{alert_id}").json()["schema_id"] == target_schema


def test_duplicating_an_entry_does_not_copy_its_alerts(http_client, acting_admin):
    database_id, schema_id, entry_id = _setup(http_client)
    _create(http_client, entry_id)

    copy = http_client.post(f"/api/databases/{database_id}/entries/{entry_id}/duplicate")

    assert copy.status_code == 201
    copy_id = copy.json()["id"]
    assert _entries(http_client, database_id)[copy_id]["values"][schema_id] == {"alerts": []}


# ─── Shifting with a moved date ───────────────────────────────────────────────


def _alert_at(http_client, entry_id: str, local: str, zone: str = "Europe/Berlin") -> str:
    from zoneinfo import ZoneInfo

    at = datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(zone))
    return _create(http_client, entry_id, at=at.isoformat()).json()["id"]


def _local_at(http_client, alert_id: str, zone: str = "Europe/Berlin") -> str:
    from zoneinfo import ZoneInfo

    at = http_client.get(f"/api/alerts/{alert_id}").json()["at"]
    moment = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(ZoneInfo(zone))
    return moment.strftime("%Y-%m-%dT%H:%M")


def _shift(http_client, entry_id: str, old: str, new: str, mode: str):
    return http_client.post(
        "/api/alerts/shift",
        json={"block_id": entry_id, "old_start": old, "new_start": new, "mode": mode},
        headers={"X-Timezone": "Europe/Berlin"},
    )


def test_shift_by_day_keeps_the_time_of_day_across_daylight_saving(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    year = datetime.now().year + 2
    alert_id = _alert_at(http_client, entry_id, f"{year}-03-20T08:15")

    resp = _shift(http_client, entry_id, f"{year}-03-22T10:00", f"{year}-04-05", "day")

    assert resp.status_code == 200
    assert _local_at(http_client, alert_id) == f"{year}-04-03T08:15"


def test_shift_exactly_keeps_the_distance(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    year = datetime.now().year + 2
    alert_id = _alert_at(http_client, entry_id, f"{year}-06-10T06:52")

    _shift(http_client, entry_id, f"{year}-06-13T11:00", f"{year}-06-20T09:30", "exact")

    assert _local_at(http_client, alert_id) == f"{year}-06-17T05:22"


def test_shift_into_the_past_marks_delivered_and_back_resets(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    alert_id = _create(http_client, entry_id, at=_future(48)).json()["id"]
    today = datetime.now().date()

    _shift(http_client, entry_id, (today + timedelta(days=3)).isoformat(),
           (today - timedelta(days=3)).isoformat(), "day")
    moved = http_client.get(f"/api/alerts/{alert_id}").json()
    assert moved["fired_at"] is not None and moved["delivered"] and moved["dismissed_at"]
    assert http_client.get("/api/alerts/pending").json() == []

    _shift(http_client, entry_id, (today - timedelta(days=3)).isoformat(),
           (today + timedelta(days=3)).isoformat(), "day")
    back = http_client.get(f"/api/alerts/{alert_id}").json()
    assert back["fired_at"] is None and not back["delivered"] and back["dismissed_at"] is None


def test_shift_moves_only_the_callers_alerts(http_client, login_as, member_user, acting_admin):
    database_id, _, entry_id = _setup(http_client)
    _share(http_client, database_id)
    year = datetime.now().year + 2
    own = _alert_at(http_client, entry_id, f"{year}-06-10T09:00")
    login_as(member_user)
    other = _create(http_client, entry_id, at=_future(24 * 30)).json()["id"]
    other_at = http_client.get(f"/api/alerts/{other}").json()["at"]

    login_as(acting_admin)
    _shift(http_client, entry_id, f"{year}-06-11", f"{year}-06-12", "day")

    assert _local_at(http_client, own) == f"{year}-06-11T09:00"
    login_as(member_user)
    assert http_client.get(f"/api/alerts/{other}").json()["at"] == other_at


def test_shift_refuses_an_unreadable_date(http_client, acting_admin):
    _, _, entry_id = _setup(http_client)
    assert _shift(http_client, entry_id, "not a date", "2045-01-01", "day").status_code == 422


def test_system_alerts_cannot_be_changed_or_deleted_but_dismissed(http_client, acting_admin):
    from app.alerts.alerts_models import KIND_SYSTEM

    _, schema_id, entry_id = _setup(http_client)
    with db_module.SessionLocal() as db:
        alert = Alert(
            block_id=uuid.UUID(entry_id), schema_id=uuid.UUID(schema_id),
            user_id=acting_admin.id, fire_at=datetime.now(timezone.utc) + timedelta(days=1),
            kind=KIND_SYSTEM, title="",
        )
        db.add(alert)
        db.commit()
        alert_id = str(alert.id)

    assert http_client.patch(f"/api/alerts/{alert_id}", json={"title": "x"}).status_code == 404
    assert http_client.delete(f"/api/alerts/{alert_id}").status_code == 404
    assert http_client.get(f"/api/alerts/{alert_id}").json()["kind"] == "system"
    assert http_client.post(f"/api/alerts/{alert_id}/dismiss").status_code == 200
