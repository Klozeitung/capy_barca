"""
Tests for deleting an uploaded icon (app.icons.service.delete_icon) below the
router: what the result reports for the notifications and which recomputations
it starts.
"""
import uuid
from datetime import datetime, timezone

import pytest

import app.database.database as db_module
from app.blocks.models import Block, BlockEvent, DatabaseSettings
from app.icons import service
from app.icons.model import CustomIcon
from app.icons.names import custom_icon_name


@pytest.fixture(autouse=True)
def icon_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "STATIC_ROOT", tmp_path / "uploads")


@pytest.fixture
def recomputed(monkeypatch):
    """Record the recomputations the deletion starts instead of running them."""
    calls: list[tuple[str, uuid.UUID, uuid.UUID]] = []
    other_database = uuid.uuid4()

    def same_db(db, database_id, entry_id):
        calls.append(("same", database_id, entry_id))

    def cross_db(db, database_id, entry_id):
        calls.append(("cross", database_id, entry_id))
        return [other_database]

    monkeypatch.setattr(service, "compute_same_db_rollup_dependents", same_db)
    monkeypatch.setattr(service, "compute_cross_db_dependents", cross_db)
    return calls, other_database


def _block(db, block_type: str, parent_id, icon=None) -> Block:
    now = datetime.now(timezone.utc)
    block = Block(
        id=uuid.uuid4(), parent_id=parent_id, type=block_type, position=1.0,
        state="active", icon=icon, created_at=now, updated_at=now,
    )
    db.add(block)
    db.flush()
    return block


def _scene():
    """An icon, a database using it as default, an entry, a page and a template."""
    with db_module.SessionLocal() as db:
        icon = CustomIcon(id=uuid.uuid4(), name="Icon A", stored_name="a.png")
        db.add(icon)
        db.flush()
        name = custom_icon_name(icon.id)
        root = _block(db, "page", None)
        database = _block(db, "database", root.id)
        db.add(DatabaseSettings(database_id=database.id, default_entry_icon="mdi:star-outline"))
        entry = _block(db, "page", database.id, name)
        template = _block(db, "entry_template", database.id, name)
        page = _block(db, "page", root.id, name)
        db.commit()
        return icon.id, name, database.id, entry.id, template.id, page.id


def test_result_names_every_changed_block_and_database(recomputed):
    calls, other_database = recomputed
    icon_id, name, database, entry, template, page = _scene()

    with db_module.SessionLocal() as db:
        result = service.delete_icon(db, db.get(CustomIcon, icon_id))
        db.commit()

    assert result.blocks == {entry: name, template: name, page: name}
    assert result.databases == [str(database), str(other_database)]
    assert calls == [("same", database, entry), ("cross", database, entry)]


def test_every_change_is_recorded_as_an_icon_change(recomputed):
    icon_id, name, database, entry, template, page = _scene()

    with db_module.SessionLocal() as db:
        service.delete_icon(db, db.get(CustomIcon, icon_id))
        db.commit()

    with db_module.SessionLocal() as db:
        events = {
            e.block_id: e.after["icon"]
            for e in db.query(BlockEvent).filter(BlockEvent.event_type == "icon_changed")
        }
    assert events == {entry: "mdi:star-outline", template: None, page: None}


def test_a_rollback_keeps_the_icon_and_its_uses(recomputed):
    icon_id, name, database, entry, template, page = _scene()

    with db_module.SessionLocal() as db:
        service.delete_icon(db, db.get(CustomIcon, icon_id))
        db.rollback()

    with db_module.SessionLocal() as db:
        assert db.get(CustomIcon, icon_id) is not None
        assert db.get(Block, entry).icon == name
        assert db.get(Block, page).icon == name


def test_removing_a_missing_file_is_fine():
    service.remove_file(CustomIcon(id=uuid.uuid4(), name="Icon A", stored_name="gone.png"))


def test_a_stored_name_outside_the_icon_directory_is_refused():
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        service.remove_file(CustomIcon(id=uuid.uuid4(), name="Icon A", stored_name="../other.png"))
