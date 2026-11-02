"""
Tests for the alert repository.

They run against the PostgreSQL test database (``isolated_db`` is autouse)
and call the repository directly.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.database.database as db_module
from app.alerts import alerts_repository as repo
from app.alerts.alerts_models import Alert
from app.blocks import repository as blocks_repo
from app.blocks.models import WORKSPACE_ROOT_ID, Block, PropertyValue
from app.users.model import User

NOW = datetime(2045, 7, 17, 9, 30, tzinfo=timezone.utc)


# ─── Fixtures and helpers ─────────────────────────────────────────────────────


@pytest.fixture
def db():
    with db_module.SessionLocal() as session:
        session.add(Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0, state="active"))
        session.commit()
        yield session


def _user(db, name: str = "person-a") -> User:
    user = User(id=uuid.uuid4(), username=name, password_hash="x", role="member", is_active=True)
    db.add(user)
    db.commit()
    return user


def _database(db) -> Block:
    database = blocks_repo.create_block(db, type="database", position=1.0, parent_id=WORKSPACE_ROOT_ID)
    db.commit()
    return database


def _entry(db, database, title: str = "Entry A", type_: str = "page") -> Block:
    entry = blocks_repo.create_block(
        db, type=type_, position=1.0, parent_id=database.id, content={"title": title},
    )
    db.commit()
    return entry


def _alerts_schema(db, database, name: str = "Reminders"):
    schema = blocks_repo.create_schema(
        db, database_id=database.id, name=name, type="alerts", position=1.0,
    )
    db.commit()
    return schema


def _alert(db, entry, schema, user, at: datetime, title: str = "") -> Alert:
    alert = repo.create_alert(
        db, block_id=entry.id, schema_id=schema.id, user_id=user.id, fire_at=at, title=title,
    )
    db.commit()
    return alert


# ─── Serialisation ────────────────────────────────────────────────────────────


def test_iso_utc_writes_utc_with_z_and_treats_naive_values_as_utc():
    local = datetime(2045, 7, 17, 11, 30, tzinfo=timezone(timedelta(hours=2)))
    assert repo.iso_utc(local) == "2045-07-17T09:30:00Z"
    assert repo.iso_utc(datetime(2045, 7, 17, 9, 30)) == "2045-07-17T09:30:00Z"
    assert repo.iso_utc(None) is None


def test_cell_value_orders_alerts_by_time(db):
    user = _user(db)
    database = _database(db)
    schema = _alerts_schema(db, database)
    entry = _entry(db, database)
    later = _alert(db, entry, schema, user, NOW + timedelta(hours=2), "Later")
    earlier = _alert(db, entry, schema, user, NOW + timedelta(hours=1), "Earlier")

    value = repo.cell_value([later, earlier])

    assert [a["title"] for a in value["alerts"]] == ["Earlier", "Later"]
    assert value["alerts"][0]["at"] == "2045-07-17T10:30:00Z"
    assert value["alerts"][0]["delivered"] is False


def test_change_payload_names_entry_database_property_and_the_users_cell(db):
    owner, other = _user(db, "person-a"), _user(db, "person-b")
    database = _database(db)
    schema = _alerts_schema(db, database)
    entry = _entry(db, database)
    _alert(db, entry, schema, owner, NOW + timedelta(hours=1), "Own")
    _alert(db, entry, schema, other, NOW + timedelta(hours=2), "Other")

    payload = repo.change_payload(db, owner.id, entry.id)

    assert payload["block_id"] == str(entry.id)
    assert payload["database_id"] == str(database.id)
    assert payload["schema_id"] == str(schema.id)
    assert [a["title"] for a in payload["value"]["alerts"]] == ["Own"]


def test_change_payload_without_alerts_property_names_none(db):
    user = _user(db)
    database = _database(db)
    entry = _entry(db, database)

    payload = repo.change_payload(db, user.id, entry.id)

    assert payload["schema_id"] is None
    assert payload["value"] == {"alerts": []}


# ─── Cell values ──────────────────────────────────────────────────────────────


def test_values_for_entries_hold_only_the_viewers_alerts(db):
    owner, other = _user(db, "person-a"), _user(db, "person-b")
    database = _database(db)
    schema = _alerts_schema(db, database)
    entry = _entry(db, database)
    _alert(db, entry, schema, owner, NOW, "Own")
    _alert(db, entry, schema, other, NOW, "Foreign")

    values = repo.values_for_entries(db, owner.id, [entry])

    cell = values[entry.id][str(schema.id)]
    assert [a["title"] for a in cell["alerts"]] == ["Own"]


def test_values_for_entries_give_an_empty_cell_without_alerts_or_viewer(db):
    user = _user(db)
    database = _database(db)
    schema = _alerts_schema(db, database)
    entry = _entry(db, database)
    _alert(db, entry, schema, user, NOW)

    assert repo.values_for_entries(db, _user(db, "person-b").id, [entry]) == {
        entry.id: {str(schema.id): {"alerts": []}}
    }
    assert repo.values_for_entries(db, None, [entry]) == {
        entry.id: {str(schema.id): {"alerts": []}}
    }


def test_values_for_entries_skip_databases_without_alerts_property(db):
    user = _user(db)
    entry = _entry(db, _database(db))
    assert repo.values_for_entries(db, user.id, [entry]) == {}


def test_values_for_entries_cost_two_queries_for_many_entries(db, count_queries):
    user = _user(db)
    database = _database(db)
    schema = _alerts_schema(db, database)
    entries = [_entry(db, database, f"Entry {i}") for i in range(12)]
    for entry in entries:
        _alert(db, entry, schema, user, NOW)
    # Loaded before counting, as the entry endpoints have them in hand.
    for entry in entries:
        db.refresh(entry)
    user_id = user.id

    with count_queries() as counter:
        values = repo.values_for_entries(db, user_id, entries)

    assert counter.count == 2
    assert len(values) == 12


# ─── Writes ───────────────────────────────────────────────────────────────────


def test_a_new_time_makes_the_alert_due_again_and_a_new_title_does_not(db):
    user = _user(db)
    database = _database(db)
    schema = _alerts_schema(db, database)
    alert = _alert(db, _entry(db, database), schema, user, NOW)
    alert.fired_at, alert.delivered, alert.dismissed_at = NOW, True, NOW
    db.commit()

    repo.update_alert(db, alert, title="Renamed")
    assert alert.fired_at == NOW and alert.delivered is True

    repo.update_alert(db, alert, fire_at=NOW + timedelta(days=1))
    assert alert.fired_at is None
    assert alert.delivered is False
    assert alert.dismissed_at is None


def test_dismiss_is_idempotent(db):
    user = _user(db)
    database = _database(db)
    alert = _alert(db, _entry(db, database), _alerts_schema(db, database), user, NOW)
    repo.dismiss_alert(db, alert, NOW)
    repo.dismiss_alert(db, alert, NOW + timedelta(hours=1))
    assert alert.dismissed_at == NOW


def test_delete_for_schema_removes_only_that_propertys_alerts(db):
    user = _user(db)
    db_a, db_b = _database(db), _database(db)
    schema_a, schema_b = _alerts_schema(db, db_a), _alerts_schema(db, db_b)
    _alert(db, _entry(db, db_a), schema_a, user, NOW)
    kept = _alert(db, _entry(db, db_b), schema_b, user, NOW)

    assert repo.delete_for_schema(db, schema_a.id) == 1
    db.commit()
    assert [a.id for a in db.query(Alert).all()] == [kept.id]


def test_transfer_moves_alerts_to_the_targets_alerts_property(db):
    user = _user(db)
    source, target = _database(db), _database(db)
    _alerts_schema(db, source)
    target_schema = _alerts_schema(db, target, "Other name")
    entry = _entry(db, source)
    alert = _alert(db, entry, repo.find_alerts_schema(db, source.id), user, NOW)

    repo.transfer_to_database(db, [entry.id], target.id)
    db.commit()

    assert db.get(Alert, alert.id).schema_id == target_schema.id


@pytest.mark.parametrize("with_target_database", [True, False])
def test_transfer_deletes_alerts_without_a_target_property(db, with_target_database):
    user = _user(db)
    source = _database(db)
    schema = _alerts_schema(db, source)
    entry = _entry(db, source)
    _alert(db, entry, schema, user, NOW)

    target_id = _database(db).id if with_target_database else None
    repo.transfer_to_database(db, [entry.id], target_id)
    db.commit()

    assert db.query(Alert).count() == 0


# ─── Cascades ─────────────────────────────────────────────────────────────────


def test_purging_the_entry_or_deleting_the_property_removes_its_alerts(db):
    user = _user(db)
    database = _database(db)
    schema = _alerts_schema(db, database)
    first, second = _entry(db, database, "Entry A"), _entry(db, database, "Entry B")
    _alert(db, first, schema, user, NOW)
    _alert(db, second, schema, user, NOW)

    db.delete(db.get(Block, first.id))
    db.commit()
    assert db.query(Alert).count() == 1

    db.delete(db.get(type(schema), schema.id))
    db.commit()
    assert db.query(Alert).count() == 0


def test_no_property_value_is_ever_written_for_an_alert(db):
    user = _user(db)
    database = _database(db)
    _alert(db, _entry(db, database), _alerts_schema(db, database), user, NOW)
    assert db.query(PropertyValue).count() == 0


# ─── Claiming ─────────────────────────────────────────────────────────────────


def test_claim_due_takes_due_alerts_once(db):
    user = _user(db)
    database = _database(db)
    schema = _alerts_schema(db, database)
    entry = _entry(db, database)
    due = _alert(db, entry, schema, user, NOW - timedelta(minutes=5))
    exact = _alert(db, entry, schema, user, NOW)
    future = _alert(db, entry, schema, user, NOW + timedelta(minutes=1))

    claimed = repo.claim_due(db, NOW)
    db.commit()

    assert {a.id for a in claimed} == {due.id, exact.id}
    assert all(a.fired_at == NOW for a in claimed)
    assert db.get(Alert, future.id).fired_at is None
    assert repo.claim_due(db, NOW) == []


def test_claim_due_respects_the_batch_limit(db):
    user = _user(db)
    database = _database(db)
    schema = _alerts_schema(db, database)
    entry = _entry(db, database)
    for minutes in range(3):
        _alert(db, entry, schema, user, NOW - timedelta(minutes=minutes))

    assert len(repo.claim_due(db, NOW, limit=2)) == 2
    db.commit()
    assert len(repo.claim_due(db, NOW, limit=2)) == 1


def test_claim_due_skips_rows_another_pass_holds(db, test_engine):
    """Two overlapping passes never claim the same alert."""
    from sqlalchemy.orm import sessionmaker

    user = _user(db)
    database = _database(db)
    schema = _alerts_schema(db, database)
    alert = _alert(db, _entry(db, database), schema, user, NOW - timedelta(minutes=1))

    other = sessionmaker(bind=test_engine)()
    try:
        first = repo.claim_due(other, NOW)
        assert [a.id for a in first] == [alert.id]
        # The first pass has not committed; its row lock is still held.
        assert repo.claim_due(db, NOW) == []
        other.commit()
    finally:
        other.close()


# ─── Listings ─────────────────────────────────────────────────────────────────


def test_upcoming_lists_unfired_alerts_on_active_entries_earliest_first(db):
    user = _user(db)
    database = _database(db)
    schema = _alerts_schema(db, database)
    active, trashed = _entry(db, database, "Entry A"), _entry(db, database, "Entry B")
    late = _alert(db, active, schema, user, NOW + timedelta(days=2))
    soon = _alert(db, active, schema, user, NOW + timedelta(days=1))
    fired = _alert(db, active, schema, user, NOW)
    fired.fired_at = NOW
    _alert(db, trashed, schema, user, NOW + timedelta(days=1))
    trashed.state = "trash"
    db.commit()

    assert [a.id for a in repo.list_upcoming(db, user.id, 10)] == [soon.id, late.id]


def test_pending_lists_delivered_alerts_not_dismissed(db):
    user = _user(db)
    database = _database(db)
    schema = _alerts_schema(db, database)
    entry = _entry(db, database)
    pending = _alert(db, entry, schema, user, NOW)
    dismissed = _alert(db, entry, schema, user, NOW)
    undelivered = _alert(db, entry, schema, user, NOW)
    for alert in (pending, dismissed, undelivered):
        alert.fired_at = NOW
    pending.delivered = dismissed.delivered = True
    dismissed.dismissed_at = NOW
    db.commit()

    assert [a.id for a in repo.list_pending(db, user.id, 10)] == [pending.id]
