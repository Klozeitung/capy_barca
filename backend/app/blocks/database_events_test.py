"""
Tests for the change events of the database API.

The helpers hand their events to ``emit_block_event``; these tests replace it
with a recorder and check what would be broadcast. Which entries changed is
recorded by ``app.blocks.entry_changes`` during the flush, so the listing
tests write through an ordinary session first.
"""
from datetime import datetime, timezone

import pytest

import app.database.database as db_module
from app.blocks import database_events
from app.blocks import entry_changes
from app.blocks import repository as repo
from app.blocks.models import WORKSPACE_ROOT_ID, Block


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


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    """Every event the helpers hand to ``emit_block_event``."""
    events: list[dict] = []

    def _record(**kwargs):
        events.append(kwargs)
        return 0

    monkeypatch.setattr(database_events, "emit_block_event", _record)
    return events


def _block(db, type_: str, parent_id, position: float = 1.0) -> Block:
    block = repo.create_block(db, type=type_, position=position, parent_id=parent_id)
    db.commit()
    return block


def _fresh(db) -> None:
    """Forget what the set-up recorded, as a new request would start."""
    db.info.pop(entry_changes._PENDING_KEY, None)


# ─── Single events ───────────────────────────────────────────────────────────


def test_entries_updated_names_the_database(db, sent):
    database = _block(db, "database", WORKSPACE_ROOT_ID)

    database_events.emit_entries_updated(db, database.id)

    assert sent == [{
        "db": db,
        "event_type": "database_entries_updated",
        "block_id": str(database.id),
        "before": None,
        "after": {"database_id": str(database.id)},
    }]


def test_entries_updated_for_keeps_the_order(db, sent):
    first = _block(db, "database", WORKSPACE_ROOT_ID)
    second = _block(db, "database", WORKSPACE_ROOT_ID, 2.0)

    database_events.emit_entries_updated_for(db, [second.id, first.id])

    assert [e["block_id"] for e in sent] == [str(second.id), str(first.id)]


def test_schema_updated_names_the_database(db, sent):
    database = _block(db, "database", WORKSPACE_ROOT_ID)

    database_events.emit_schema_updated(db, database.id)

    assert sent[0]["event_type"] == "database_schema_updated"
    assert sent[0]["after"] == {"database_id": str(database.id)}


def test_entry_created_describes_the_new_block(db, sent):
    database = _block(db, "database", WORKSPACE_ROOT_ID)
    entry = _block(db, "page", database.id)

    database_events.emit_entry_created(db, database.id, entry)

    assert sent[0]["event_type"] == "created"
    assert sent[0]["block_id"] == str(entry.id)
    assert sent[0]["after"]["parent_id"] == str(database.id)
    assert sent[0]["after"]["type"] == "page"


# ─── Listing changes ─────────────────────────────────────────────────────────


def test_listing_changes_announce_a_database_that_gained_an_entry(db, sent):
    database = _block(db, "database", WORKSPACE_ROOT_ID)
    _fresh(db)
    _block(db, "page", database.id)

    announced = database_events.emit_listing_changes(db)

    assert announced == [database.id]
    assert [(e["event_type"], e["block_id"]) for e in sent] == [
        ("database_entries_updated", str(database.id)),
    ]


def test_listing_changes_leave_out_parents_that_are_no_databases(db, sent):
    page = _block(db, "page", WORKSPACE_ROOT_ID)
    _fresh(db)
    _block(db, "paragraph", page.id)

    assert database_events.emit_listing_changes(db) == []
    assert sent == []


def test_listing_changes_announce_both_ends_of_a_move(db, sent):
    source = _block(db, "database", WORKSPACE_ROOT_ID)
    target = _block(db, "database", WORKSPACE_ROOT_ID, 2.0)
    entry = _block(db, "page", source.id)
    _fresh(db)
    entry.parent_id = target.id
    db.commit()

    announced = database_events.emit_listing_changes(db)

    assert announced == sorted([source.id, target.id], key=str)
    assert [e["block_id"] for e in sent] == [str(i) for i in announced]


def test_listing_changes_skip_databases_already_announced(db, sent):
    first = _block(db, "database", WORKSPACE_ROOT_ID)
    second = _block(db, "database", WORKSPACE_ROOT_ID, 2.0)
    _fresh(db)
    _block(db, "page", first.id)
    _block(db, "page", second.id)

    announced = database_events.emit_listing_changes(db, announced=[str(first.id)])

    assert announced == [second.id]
    assert [e["block_id"] for e in sent] == [str(second.id)]


def test_listing_changes_announce_nothing_without_a_change(db, sent):
    _block(db, "database", WORKSPACE_ROOT_ID)
    _fresh(db)

    assert database_events.emit_listing_changes(db) == []
    assert sent == []


def test_listing_changes_announce_nothing_after_a_rollback(db, sent):
    database = _block(db, "database", WORKSPACE_ROOT_ID)
    _fresh(db)
    repo.create_block(db, type="page", position=2.0, parent_id=database.id)
    db.flush()
    db.rollback()

    assert database_events.emit_listing_changes(db) == []
    assert sent == []
