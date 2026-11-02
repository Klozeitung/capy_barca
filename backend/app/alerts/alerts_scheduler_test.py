"""
Tests for the alert scheduler.

``run_pass`` is driven directly against the PostgreSQL test database; the
loop and the WebSocket delivery are tested with the connection manager and a
stub socket, in the synchronous style of ``ws/broadcaster_test.py``.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.database.database as db_module
from app.alerts import alerts_repository as repo
from app.alerts import alerts_scheduler as scheduler
from app.alerts.alerts_models import Alert
from app.blocks import repository as blocks_repo
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.permissions import repository as perm_repo
from app.users.model import User
from app.ws.manager import manager

NOW = datetime(2045, 7, 17, 9, 30, tzinfo=timezone.utc)


# ─── Fixtures and helpers ─────────────────────────────────────────────────────


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def accept(self) -> None:
        pass

    async def send_text(self, message: str) -> None:
        self.sent.append(message)

    async def close(self, code: int = 1000) -> None:
        pass


@pytest.fixture(autouse=True)
def clean_manager():
    manager._connections.clear()
    yield
    manager._connections.clear()


@pytest.fixture
def db():
    with db_module.SessionLocal() as session:
        session.add(Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0, state="active"))
        session.flush()
        perm_repo.set_permission(session, WORKSPACE_ROOT_ID, "everyone", [])
        session.commit()
        yield session


def _user(db, name: str = "person-a", *, role: str = "member", active: bool = True) -> User:
    user = User(id=uuid.uuid4(), username=name, password_hash="x", role=role, is_active=active)
    db.add(user)
    db.commit()
    return user


def _setup(db):
    """A database with an alerts property and one entry in it."""
    database = blocks_repo.create_block(db, type="database", position=1.0, parent_id=WORKSPACE_ROOT_ID)
    db.flush()
    schema = blocks_repo.create_schema(
        db, database_id=database.id, name="Reminders", type="alerts", position=1.0,
    )
    entry = blocks_repo.create_block(
        db, type="page", position=1.0, parent_id=database.id, content={"title": "Entry A"},
    )
    db.commit()
    return database, schema, entry


def _alert(db, entry, schema, user, at, title: str = "") -> Alert:
    alert = repo.create_alert(
        db, block_id=entry.id, schema_id=schema.id, user_id=user.id, fire_at=at, title=title,
    )
    db.commit()
    return alert


# ─── Delivery rules ───────────────────────────────────────────────────────────


def test_a_due_alert_is_delivered_with_what_a_notification_shows(db):
    user = _user(db)
    database, schema, entry = _setup(db)
    alert = _alert(db, entry, schema, user, NOW - timedelta(minutes=1), "Title A")

    deliveries = scheduler.run_pass(db, NOW)

    assert len(deliveries) == 1
    recipient, payload = deliveries[0]
    assert recipient == user.id
    assert payload["id"] == str(alert.id)
    assert payload["title"] == "Title A"
    assert payload["block_title"] == "Entry A"
    assert payload["database_id"] == str(database.id)
    db.expire_all()
    stored = db.get(Alert, alert.id)
    assert stored.fired_at == NOW and stored.delivered is True


def test_an_alert_is_never_delivered_twice(db):
    user = _user(db)
    _, schema, entry = _setup(db)
    _alert(db, entry, schema, user, NOW)

    assert len(scheduler.run_pass(db, NOW)) == 1
    assert scheduler.run_pass(db, NOW + timedelta(minutes=1)) == []


def test_a_future_alert_waits(db):
    user = _user(db)
    _, schema, entry = _setup(db)
    alert = _alert(db, entry, schema, user, NOW + timedelta(seconds=30))

    assert scheduler.run_pass(db, NOW) == []
    db.expire_all()
    assert db.get(Alert, alert.id).fired_at is None


def test_an_alert_missed_by_a_downtime_is_delivered_late_within_the_grace_period(db):
    user = _user(db)
    _, schema, entry = _setup(db)
    _alert(db, entry, schema, user, NOW - scheduler.GRACE_PERIOD)

    assert len(scheduler.run_pass(db, NOW)) == 1


def test_an_alert_beyond_the_grace_period_is_discarded(db):
    user = _user(db)
    _, schema, entry = _setup(db)
    alert = _alert(db, entry, schema, user, NOW - scheduler.GRACE_PERIOD - timedelta(minutes=1))

    assert scheduler.run_pass(db, NOW) == []
    db.expire_all()
    stored = db.get(Alert, alert.id)
    assert stored.fired_at == NOW and stored.delivered is False


def test_an_alert_on_a_trashed_entry_is_discarded_and_not_repeated_after_restore(db):
    user = _user(db)
    _, schema, entry = _setup(db)
    _alert(db, entry, schema, user, NOW)
    entry.state = "trash"
    db.commit()

    assert scheduler.run_pass(db, NOW) == []
    entry.state = "active"
    db.commit()
    assert scheduler.run_pass(db, NOW + timedelta(minutes=1)) == []


def test_an_alert_whose_account_lost_read_access_is_discarded(db):
    owner = _user(db, "person-a")
    user = _user(db, "person-b")
    database, schema, entry = _setup(db)
    _alert(db, entry, schema, user, NOW)
    database.owner_id = owner.id
    perm_repo.set_permission(db, database.id, "private", [])
    db.commit()

    assert scheduler.run_pass(db, NOW) == []


def test_a_reader_still_receives_the_alert(db):
    owner = _user(db, "person-a")
    reader = _user(db, "person-b")
    database, schema, entry = _setup(db)
    _alert(db, entry, schema, reader, NOW)
    database.owner_id = owner.id
    perm_repo.set_permission(db, database.id, "whitelist", {reader.id: "read"})
    db.commit()

    assert [r for r, _ in scheduler.run_pass(db, NOW)] == [reader.id]


def test_an_alert_of_a_deactivated_account_is_discarded(db):
    user = _user(db, active=False)
    _, schema, entry = _setup(db)
    _alert(db, entry, schema, user, NOW)

    assert scheduler.run_pass(db, NOW) == []


def test_an_alert_on_an_entry_template_is_discarded(db):
    user = _user(db)
    database, schema, _ = _setup(db)
    template = blocks_repo.create_block(db, type="entry_template", position=2.0, parent_id=database.id)
    db.commit()
    _alert(db, template, schema, user, NOW)

    assert scheduler.run_pass(db, NOW) == []


def test_a_pass_works_through_more_alerts_than_one_batch(db, monkeypatch):
    monkeypatch.setattr(repo, "CLAIM_BATCH", 2)
    user = _user(db)
    _, schema, entry = _setup(db)
    for minutes in range(5):
        _alert(db, entry, schema, user, NOW - timedelta(minutes=minutes))

    assert len(scheduler.run_pass(db, NOW)) == 5


# ─── Timing and switch ────────────────────────────────────────────────────────


def test_next_tick_is_just_past_the_next_full_minute():
    moment = datetime(2045, 7, 17, 9, 30, 45, tzinfo=timezone.utc)
    assert scheduler.seconds_until_next_tick(moment) == 15 + scheduler.TICK_OFFSET_SECONDS


@pytest.mark.parametrize("value,expected", [
    (None, True), ("true", True), ("false", False), ("0", False), ("No", False),
])
def test_the_loop_can_be_switched_off(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(scheduler.ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(scheduler.ENABLED_ENV, value)
    assert scheduler.is_enabled() is expected


def test_start_returns_nothing_when_switched_off(monkeypatch):
    monkeypatch.setenv(scheduler.ENABLED_ENV, "false")

    async def scenario():
        return scheduler.start()

    assert asyncio.run(scenario()) is None


def test_start_and_stop_run_and_cancel_the_loop(monkeypatch):
    monkeypatch.setenv(scheduler.ENABLED_ENV, "true")
    passes: list[int] = []

    async def fake_tick(now=None):
        passes.append(1)
        return 0

    monkeypatch.setattr(scheduler, "tick", fake_tick)

    async def scenario():
        task = scheduler.start()
        await asyncio.sleep(0.05)
        await scheduler.stop(task)
        return task

    task = asyncio.run(scenario())
    assert passes == [1]
    assert task.cancelled()


def test_a_failing_pass_does_not_end_the_loop(monkeypatch):
    calls: list[int] = []

    async def failing_tick(now=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return 0

    monkeypatch.setattr(scheduler, "tick", failing_tick)
    monkeypatch.setattr(scheduler, "seconds_until_next_tick", lambda now: 0.0)

    async def scenario():
        task = asyncio.get_running_loop().create_task(scheduler.run_forever())
        while len(calls) < 2:
            await asyncio.sleep(0.01)
        await scheduler.stop(task)

    asyncio.run(scenario())
    assert len(calls) >= 2


# ─── Delivery over the WebSocket ──────────────────────────────────────────────


def test_tick_sends_the_alert_to_the_owners_connections_only(db):
    owner = _user(db, "person-a")
    other = _user(db, "person-b")
    _, schema, entry = _setup(db)
    _alert(db, entry, schema, owner, NOW, "Title A")

    async def scenario():
        mine, theirs = MockWebSocket(), MockWebSocket()
        await manager.connect(mine, owner.id)
        await manager.connect(theirs, other.id)
        delivered = await scheduler.tick(NOW)
        await manager.flush()
        return delivered, mine, theirs

    delivered, mine, theirs = asyncio.run(scenario())

    assert delivered == 1
    assert theirs.sent == []
    message = json.loads(mine.sent[0])
    assert message["type"] == scheduler.FIRED_MESSAGE
    assert message["payload"]["title"] == "Title A"


def test_tick_marks_the_alert_delivered_for_an_offline_account(db):
    """Without a connection the alert stays pending for the next login."""
    user = _user(db)
    _, schema, entry = _setup(db)
    alert = _alert(db, entry, schema, user, NOW)

    assert asyncio.run(scheduler.tick(NOW)) == 1
    db.expire_all()
    assert [a.id for a in repo.list_pending(db, user.id, 10)] == [alert.id]
