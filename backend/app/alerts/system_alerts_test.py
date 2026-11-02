"""
Tests for the system alerts of calendar dates: their times, the session hook
that keeps them in line with every write, the calendar mark that switches
them on, repeating dates, the startup reconcile and the messages a change
sends to its account.

HTTP-level tests act as the persisted admin (``acting_admin``) in the zone
Europe/Berlin, sent as the X-Timezone header the web app sends.
"""
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

import app.database.database as db_module
from app.alerts import system_alerts
from app.alerts.alerts_models import KIND_SYSTEM, Alert
from app.blocks.models import WORKSPACE_ROOT_ID, PropertySchema

BERLIN = ZoneInfo("Europe/Berlin")
UTC = timezone.utc
HEADERS = {"X-Timezone": "Europe/Berlin"}


# ─── Times ────────────────────────────────────────────────────────────────────


NOW = datetime(2045, 7, 1, 12, 0, tzinfo=UTC)


def test_a_timed_date_fires_30_minutes_before_it_begins():
    fires = system_alerts.next_fire_time({"start": "2045-08-17T09:00"}, BERLIN, NOW)
    assert fires == datetime(2045, 8, 17, 6, 30, tzinfo=UTC)


def test_a_utc_stored_date_gives_the_same_time():
    fires = system_alerts.next_fire_time({"start": "2045-08-17T07:00:00Z"}, BERLIN, NOW)
    assert fires == datetime(2045, 8, 17, 6, 30, tzinfo=UTC)


def test_an_all_day_date_fires_at_18_local_the_day_before():
    fires = system_alerts.next_fire_time({"start": "2045-12-24", "end": "2045-12-26"}, BERLIN, NOW)
    assert fires == datetime(2045, 12, 23, 17, 0, tzinfo=UTC)


def test_a_date_that_has_begun_has_no_alert():
    assert system_alerts.next_fire_time({"start": "2045-06-30T09:00"}, BERLIN, NOW) is None


def test_a_date_beginning_soon_gets_an_alert_already_due():
    fires = system_alerts.next_fire_time({"start": "2045-07-01T14:10"}, BERLIN, NOW)
    assert fires == datetime(2045, 7, 1, 11, 40, tzinfo=UTC)
    assert fires < NOW


def test_a_repeating_date_fires_for_its_next_occurrence():
    value = {"start": "2045-01-02T09:00", "repeat": "weekly"}
    fires = system_alerts.next_fire_time(value, BERLIN, NOW)
    assert fires == datetime(2045, 7, 3, 6, 30, tzinfo=UTC)


def test_a_fired_occurrence_is_skipped():
    value = {"start": "2045-01-02T09:00", "repeat": "weekly"}
    fired = datetime(2045, 7, 3, 6, 30, tzinfo=UTC)
    fires = system_alerts.next_fire_time(value, BERLIN, NOW, skip=[fired])
    assert fires == datetime(2045, 7, 10, 6, 30, tzinfo=UTC)


def test_no_start_no_alert():
    assert system_alerts.next_fire_time({"start": ""}, BERLIN, NOW) is None
    assert system_alerts.next_fire_time(None, BERLIN, NOW) is None


# ─── Through the API ──────────────────────────────────────────────────────────


@pytest.fixture
def acting_admin(http_client, admin_user):
    admin_user.timezone = "Europe/Berlin"
    with db_module.SessionLocal() as db:
        db.merge(admin_user)
        db.commit()
    return admin_user


def _calendar_database(http_client) -> tuple[str, dict]:
    """A database with a calendar view; returns its id and its date property."""
    resp = http_client.post(
        "/api/blocks", json={"type": "database", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    database_id = resp.json()["id"]
    resp = http_client.post(
        f"/api/databases/{database_id}/views",
        json={"name": "", "view_type": "calendar", "calendar_date_name": "When"},
        headers=HEADERS,
    )
    assert resp.status_code == 201, resp.text
    schema_id = resp.json()["settings"]["calendarDateSchemaId"]
    # Listing the views creates the system properties (created_by and so on),
    # as the web app does when it opens a database.
    http_client.get(f"/api/databases/{database_id}/views")
    schemas = http_client.get(f"/api/databases/{database_id}/schemas").json()
    return database_id, next(s for s in schemas if s["id"] == schema_id)


def _future(days: int, clock: str = "09:00") -> str:
    day = (datetime.now(BERLIN) + timedelta(days=days)).date()
    return f"{day.isoformat()}T{clock}"


def _system_alerts(entry_id: str) -> list[Alert]:
    with db_module.SessionLocal() as db:
        return list(db.scalars(
            select(Alert).where(Alert.block_id == uuid.UUID(entry_id), Alert.kind == KIND_SYSTEM)
        ))


def _write_date(http_client, database_id, entry_id, schema_id, value):
    resp = http_client.put(
        f"/api/databases/{database_id}/entries/{entry_id}/values/{schema_id}",
        json={"value": value}, headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["value"]


def test_a_calendar_view_creates_and_marks_its_date_property(http_client, acting_admin):
    _, schema = _calendar_database(http_client)
    assert schema["name"] == "When"
    assert schema["type"] == "date"
    assert schema["config"]["calendarDate"] is True


def test_writing_a_calendar_date_sets_its_system_alert(http_client, acting_admin):
    database_id, schema = _calendar_database(http_client)
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    local = _future(10)

    shown = _write_date(http_client, database_id, entry_id, schema["id"], {"start": local, "end": local})

    assert shown["start"] == local
    [alert] = _system_alerts(entry_id)
    begins = datetime.fromisoformat(local).replace(tzinfo=BERLIN)
    assert alert.fire_at == (begins - timedelta(minutes=30)).astimezone(UTC)
    assert alert.user_id == acting_admin.id


def test_moving_the_date_moves_the_alert_and_clearing_it_removes_it(http_client, acting_admin):
    database_id, schema = _calendar_database(http_client)
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": _future(10)})
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": _future(12, "15:45")})

    [alert] = _system_alerts(entry_id)
    begins = datetime.fromisoformat(_future(12, "15:45")).replace(tzinfo=BERLIN)
    assert alert.fire_at == (begins - timedelta(minutes=30)).astimezone(UTC)

    _write_date(http_client, database_id, entry_id, schema["id"], None)
    assert _system_alerts(entry_id) == []


def test_system_alerts_are_not_shown_in_the_alerts_property(http_client, acting_admin):
    database_id, schema = _calendar_database(http_client)
    alerts_schema = http_client.post(
        f"/api/databases/{database_id}/schemas", json={"name": "Reminders", "type": "alerts"},
    ).json()
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": _future(3)})

    entries = http_client.get(f"/api/databases/{database_id}/entries").json()
    [row] = [e for e in entries if e["id"] == entry_id]
    assert row["values"][alerts_schema["id"]] == {"alerts": []}
    assert len(_system_alerts(entry_id)) == 1
    upcoming = http_client.get("/api/alerts/upcoming").json()
    assert [a["kind"] for a in upcoming] == ["system"]


def test_a_date_property_without_calendar_view_gets_no_alert(http_client, acting_admin):
    resp = http_client.post(
        "/api/blocks", json={"type": "database", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    database_id = resp.json()["id"]
    schema = http_client.post(
        f"/api/databases/{database_id}/schemas", json={"name": "Birthday", "type": "date"},
    ).json()
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": _future(3)})
    assert _system_alerts(entry_id) == []


def test_removing_the_calendar_view_removes_mark_and_alerts(http_client, acting_admin):
    database_id, schema = _calendar_database(http_client)
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": _future(3)})
    views = http_client.get(f"/api/databases/{database_id}/views").json()["views"]
    calendar = next(v for v in views if v["view_type"] == "calendar")

    resp = http_client.patch(
        f"/api/databases/{database_id}/views/{calendar['id']}", json={"view_type": "table"},
    )

    assert resp.status_code == 200
    schemas = http_client.get(f"/api/databases/{database_id}/schemas").json()
    marked = next(s for s in schemas if s["id"] == schema["id"])
    assert not (marked["config"] or {}).get("calendarDate")
    assert _system_alerts(entry_id) == []


def test_a_marked_date_property_cannot_be_deleted_retyped_or_timelined(http_client, acting_admin):
    database_id, schema = _calendar_database(http_client)
    url = f"/api/databases/{database_id}/schemas/{schema['id']}"
    assert http_client.delete(url).status_code == 422
    assert http_client.patch(url, json={"type": "text"}).status_code == 422
    assert http_client.patch(
        url, json={"config": {**schema["config"], "hasTimeline": True}},
    ).status_code == 422
    # Renaming and other settings stay possible, and the mark survives a
    # config the client sends without it.
    resp = http_client.patch(url, json={"name": "Renamed", "config": {"includeTime": False}})
    assert resp.status_code == 200
    assert resp.json()["config"]["calendarDate"] is True


def test_a_calendar_cannot_use_a_date_property_with_a_timeline(http_client, acting_admin):
    resp = http_client.post(
        "/api/blocks", json={"type": "database", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    database_id = resp.json()["id"]
    timelined = http_client.post(
        f"/api/databases/{database_id}/schemas",
        json={"name": "Period", "type": "date", "config": {"hasTimeline": True}},
    ).json()
    resp = http_client.post(
        f"/api/databases/{database_id}/views",
        json={"view_type": "calendar", "settings": {"calendarDateSchemaId": timelined["id"]}},
    )
    assert resp.status_code == 422


def test_a_calendar_takes_an_existing_date_property_without_timeline(http_client, acting_admin):
    resp = http_client.post(
        "/api/blocks", json={"type": "database", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    database_id = resp.json()["id"]
    existing = http_client.post(
        f"/api/databases/{database_id}/schemas", json={"name": "Day", "type": "date"},
    ).json()
    resp = http_client.post(
        f"/api/databases/{database_id}/views",
        json={"view_type": "calendar", "calendar_date_name": "When"},
    )
    assert resp.json()["settings"]["calendarDateSchemaId"] == existing["id"]


def test_the_recipient_is_the_entry_creator(http_client, login_as, member_user, acting_admin):
    database_id, schema = _calendar_database(http_client)
    http_client.put(
        f"/api/blocks/{database_id}/permissions", json={"mode": "everyone", "grants": []},
    )
    login_as(member_user)
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": _future(3)})
    [alert] = _system_alerts(entry_id)
    assert alert.user_id == member_user.id


def test_a_zone_change_moves_the_accounts_alerts(http_client, acting_admin):
    database_id, schema = _calendar_database(http_client)
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    day = _future(10)[:10]
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": day})
    [before] = _system_alerts(entry_id)

    resp = http_client.patch("/api/users/me/timezone", json={"timezone": "America/New_York"})

    assert resp.status_code == 200
    [after] = _system_alerts(entry_id)
    expected = datetime.fromisoformat(day).replace(hour=18, tzinfo=ZoneInfo("America/New_York"))
    assert after.fire_at == (expected - timedelta(days=1)).astimezone(UTC)
    assert after.fire_at != before.fire_at


# ─── Reconcile and repeating dates ────────────────────────────────────────────


def test_reconcile_adds_missing_alerts_and_drops_stray_ones(http_client, acting_admin):
    database_id, schema = _calendar_database(http_client)
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": _future(4)})
    with db_module.SessionLocal() as db:
        for alert in db.scalars(select(Alert)):
            db.delete(alert)
        db.commit()

    with db_module.SessionLocal() as db:
        system_alerts.reconcile(db)
        system_alerts.reconcile(db)

    assert len(_system_alerts(entry_id)) == 1


def test_a_fired_repeating_alert_gets_the_next_occurrence(http_client, acting_admin):
    from app.alerts import alerts_scheduler

    database_id, schema = _calendar_database(http_client)
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    first = _future(1)
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": first, "repeat": "daily"})
    [alert] = _system_alerts(entry_id)

    with db_module.SessionLocal() as db:
        alerts_scheduler.run_pass(db, alert.fire_at + timedelta(seconds=1))

    rows = sorted(_system_alerts(entry_id), key=lambda a: a.fire_at)
    assert len(rows) == 2
    assert rows[0].fired_at is not None and rows[0].delivered
    assert rows[1].fired_at is None
    assert rows[1].fire_at == rows[0].fire_at + timedelta(days=1)


def test_moving_an_entry_out_of_the_calendar_drops_its_system_alert(http_client, acting_admin):
    database_id, schema = _calendar_database(http_client)
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": _future(4)})
    target = http_client.post(
        "/api/blocks", json={"type": "database", "parent_id": str(WORKSPACE_ROOT_ID)},
    ).json()["id"]

    resp = http_client.post(
        f"/api/blocks/{entry_id}/move", json={"new_parent_id": target, "new_position": 1.0},
    )

    assert resp.status_code == 200
    assert _system_alerts(entry_id) == []


# ─── Calendars older than the marks ───────────────────────────────────────────


def _unmark(schema_id: str) -> None:
    """Remove the calendar mark and the system alerts, as before the marks existed."""
    from app.blocks.views import CALENDAR_DATE_KEY

    with db_module.SessionLocal() as db:
        schema = db.get(PropertySchema, uuid.UUID(schema_id))
        config = dict(schema.config or {})
        config.pop(CALENDAR_DATE_KEY, None)
        schema.config = config
        for alert in db.scalars(select(Alert).where(Alert.kind == KIND_SYSTEM)):
            db.delete(alert)
        db.commit()


def test_reconcile_marks_the_date_property_of_an_older_calendar(http_client, acting_admin):
    database_id, schema = _calendar_database(http_client)
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": _future(3, "10:30")})
    _unmark(schema["id"])
    assert _system_alerts(entry_id) == []

    with db_module.SessionLocal() as db:
        system_alerts.reconcile(db)

    with db_module.SessionLocal() as db:
        assert db.get(PropertySchema, uuid.UUID(schema["id"])).config["calendarDate"] is True
    assert len(_system_alerts(entry_id)) == 1


def test_reconcile_drops_a_mark_no_calendar_uses(http_client, acting_admin):
    database_id, schema = _calendar_database(http_client)
    view_id = http_client.get(f"/api/databases/{database_id}/views").json()["views"][0]["id"]
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": _future(3)})
    with db_module.SessionLocal() as db:
        from app.blocks.models import DatabaseView

        view = db.get(DatabaseView, uuid.UUID(view_id))
        view.view_type = "table"
        db.commit()

    with db_module.SessionLocal() as db:
        system_alerts.reconcile(db)

    with db_module.SessionLocal() as db:
        assert "calendarDate" not in (db.get(PropertySchema, uuid.UUID(schema["id"])).config or {})
    assert _system_alerts(entry_id) == []


# ─── Messages ─────────────────────────────────────────────────────────────────


@pytest.fixture
def sent(monkeypatch):
    """Collect what goes out as alert.changed instead of sending it."""
    import app.ws.broadcaster as broadcaster

    messages: list[tuple] = []
    monkeypatch.setattr(
        broadcaster, "emit_user_message",
        lambda user_id, message_type, payload, **_: messages.append((user_id, message_type, payload)) or 1,
    )
    return messages


def test_a_new_system_alert_tells_its_account_after_the_commit(http_client, acting_admin, sent):
    database_id, schema = _calendar_database(http_client)
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    sent.clear()

    _write_date(http_client, database_id, entry_id, schema["id"], {"start": _future(2, "08:00")})

    changes = [m for m in sent if m[1] == "alert.changed"]
    assert len(changes) == 1
    user_id, _, payload = changes[0]
    assert user_id == acting_admin.id
    assert payload["block_id"] == entry_id
    assert payload["database_id"] == database_id
    # System alerts never appear in the cell.
    assert payload["value"] == {"alerts": []}


def test_an_unchanged_system_alert_sends_nothing(http_client, acting_admin, sent):
    database_id, schema = _calendar_database(http_client)
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": _future(2, "08:00")})
    sent.clear()

    with db_module.SessionLocal() as db:
        system_alerts.reconcile(db)

    assert [m for m in sent if m[1] == "alert.changed"] == []


def test_a_rolled_back_change_sends_nothing(http_client, acting_admin, sent):
    database_id, schema = _calendar_database(http_client)
    entry_id = http_client.post(f"/api/databases/{database_id}/entries").json()["id"]
    _write_date(http_client, database_id, entry_id, schema["id"], {"start": _future(2, "08:00")})
    sent.clear()

    with db_module.SessionLocal() as db:
        for alert in db.scalars(select(Alert).where(Alert.kind == KIND_SYSTEM)):
            db.delete(alert)
        db.flush()
        schema_row = db.get(PropertySchema, uuid.UUID(schema["id"]))
        system_alerts.sync_entry(db, uuid.UUID(entry_id), schema_row)
        db.rollback()

    assert sent == []
    assert len(_system_alerts(entry_id)) == 1
