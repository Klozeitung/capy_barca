"""
Tests for the record of changed entries behind ``database_entries_updated``.

The record is filled by a flush listener, so these tests write through an
ordinary session and read back what :func:`entry_changes.describe` reports.
"""
import uuid
from datetime import datetime, timezone

import pytest

import app.database.database as db_module
from app.blocks import entry_changes
from app.blocks import repository as repo
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.users.model import User


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def db():
    with db_module.SessionLocal() as session:
        session.add(Block(
            id=WORKSPACE_ROOT_ID, type="workspace", position=0.0, state="active",
            content={"title": "Workspace"}, created_at=_now(), updated_at=_now(),
        ))
        session.commit()
        yield session


def _database(db) -> Block:
    database = repo.create_block(db, type="database", position=1.0, parent_id=WORKSPACE_ROOT_ID)
    db.commit()
    return database


def _entry(db, database, title="Entry A") -> Block:
    entry = repo.create_block(
        db, type="page", position=1.0, parent_id=database.id, content={"title": title},
    )
    db.commit()
    return entry


def _schema(db, database, name="Text"):
    schema = repo.create_schema(db, database_id=database.id, name=name, type="text", position=1.0)
    db.commit()
    return schema


def _fresh(db) -> None:
    """Forget what the set-up recorded, as a new request would start."""
    db.info.pop(entry_changes._PENDING_KEY, None)


# ─── Values and titles ───────────────────────────────────────────────────────


def test_written_values_are_reported_per_entry_and_property(db):
    database = _database(db)
    first, second = _schema(db, database, "First"), _schema(db, database, "Second")
    entry = _entry(db, database)
    _fresh(db)

    repo.upsert_value(db, page_id=entry.id, schema_id=first.id, value={"text": "a"})
    repo.upsert_value(db, page_id=entry.id, schema_id=second.id, value={"text": "b"})
    db.commit()

    assert entry_changes.describe(db, database.id) == {
        "entries": {str(entry.id): sorted([str(first.id), str(second.id)])},
    }


def test_rewriting_a_value_is_reported_and_an_unchanged_one_is_not(db):
    database = _database(db)
    text = _schema(db, database)
    changed, untouched = _entry(db, database), _entry(db, database, "Entry B")
    repo.upsert_value(db, page_id=changed.id, schema_id=text.id, value={"text": "a"})
    repo.upsert_value(db, page_id=untouched.id, schema_id=text.id, value={"text": "a"})
    db.commit()
    _fresh(db)

    repo.upsert_value(db, page_id=changed.id, schema_id=text.id, value={"text": "b"})
    db.commit()

    assert entry_changes.describe(db, database.id)["entries"] == {
        str(changed.id): [str(text.id)],
    }


def test_title_change_is_reported_under_the_name_key(db):
    database = _database(db)
    entry = _entry(db, database)
    _fresh(db)

    entry.content = {"title": "Entry B"}
    db.commit()

    assert entry_changes.describe(db, database.id)["entries"] == {
        str(entry.id): [entry_changes.NAME_KEY],
    }


def test_changes_of_another_database_are_not_reported(db):
    database, other = _database(db), _database(db)
    text = _schema(db, other)
    entry = _entry(db, other)
    _fresh(db)

    repo.upsert_value(db, page_id=entry.id, schema_id=text.id, value={"text": "a"})
    db.commit()

    assert entry_changes.describe(db, database.id) == {}
    assert str(entry.id) in entry_changes.describe(db, other.id)["entries"]


def test_too_many_entries_leave_the_list_out(db, monkeypatch):
    monkeypatch.setattr(entry_changes, "MAX_ENTRIES", 2)
    database = _database(db)
    text = _schema(db, database)
    entries = [_entry(db, database, f"Entry {i}") for i in range(3)]
    _fresh(db)

    for entry in entries:
        repo.upsert_value(db, page_id=entry.id, schema_id=text.id, value={"text": "a"})
    db.commit()

    assert "entries" not in entry_changes.describe(db, database.id)


# ─── Listing ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("change", ["create", "trash", "reorder", "move", "delete"])
def test_changes_to_the_list_of_entries_are_flagged(db, change):
    database = _database(db)
    other = _database(db)
    entry = _entry(db, database)
    _fresh(db)

    if change == "create":
        _entry(db, database, "Entry B")
    elif change == "trash":
        entry.state = "trash"
    elif change == "reorder":
        entry.position = 5.0
    elif change == "move":
        entry.parent_id = other.id
    elif change == "delete":
        db.delete(entry)
    db.commit()

    assert entry_changes.describe(db, database.id)["listing"] is True


def test_moving_an_entry_flags_both_databases(db):
    database, other = _database(db), _database(db)
    entry = _entry(db, database)
    _fresh(db)

    entry.parent_id = other.id
    db.commit()

    assert entry_changes.describe(db, database.id)["listing"] is True
    assert entry_changes.describe(db, other.id)["listing"] is True


# ─── Lifetime and actor ──────────────────────────────────────────────────────


def test_rollback_forgets_what_was_recorded(db):
    database = _database(db)
    text = _schema(db, database)
    entry = _entry(db, database)
    _fresh(db)

    repo.upsert_value(db, page_id=entry.id, schema_id=text.id, value={"text": "a"})
    db.rollback()

    assert entry_changes.describe(db, database.id) == {}


def test_describing_twice_gives_the_same_answer(db):
    database = _database(db)
    text = _schema(db, database)
    entry = _entry(db, database)
    _fresh(db)

    repo.upsert_value(db, page_id=entry.id, schema_id=text.id, value={"text": "a"})
    db.commit()

    assert entry_changes.describe(db, database.id) == entry_changes.describe(db, database.id)


def test_actor_is_named_when_known(db):
    database = _database(db)
    user = User(id=uuid.uuid4(), username="person-a", password_hash="x", role="member")
    entry_changes.set_actor(db, user)

    assert entry_changes.describe(db, database.id)["actor"] == {
        "id": str(user.id), "name": "person-a",
    }


def test_unparseable_database_id_reports_nothing_but_the_actor(db):
    assert entry_changes.describe(db, "not-a-uuid") == {}
