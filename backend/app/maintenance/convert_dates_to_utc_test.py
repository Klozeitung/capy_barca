"""Tests for the one-off conversion of stored times of day to UTC."""
import uuid
from zoneinfo import ZoneInfo

import pytest

import app.database.database as db_module
from app.blocks import repository as repo
from app.blocks.models import WORKSPACE_ROOT_ID, Block, PropertyValue
from app.maintenance import convert_dates_to_utc as script

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture
def scene():
    """A database with a date and a text property and two entries."""
    with db_module.SessionLocal() as db:
        db.add(Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0, state="active"))
        db.flush()
        database = repo.create_block(db, type="database", position=1.0, parent_id=WORKSPACE_ROOT_ID)
        db.flush()
        when = repo.create_schema(db, database_id=database.id, name="When", type="date", position=1.0)
        note = repo.create_schema(db, database_id=database.id, name="Note", type="text", position=2.0)
        winter = repo.create_block(db, type="page", position=1.0, parent_id=database.id)
        summer = repo.create_block(db, type="page", position=2.0, parent_id=database.id)
        db.flush()
        repo.upsert_value(db, page_id=winter.id, schema_id=when.id,
                          value={"start": "2045-01-17T09:00", "end": "2045-01-17T10:00"})
        repo.upsert_value(db, page_id=summer.id, schema_id=when.id,
                          value={"start": "2045-08-17T09:00", "repeat": "none",
                                 "_timeline": {"2045→": {"start": "2045-08-18T09:00"}}})
        repo.upsert_value(db, page_id=winter.id, schema_id=note.id, value={"text": "2045-01-17T09:00"})
        moved = repo.create_block(
            db, type="page", position=3.0, parent_id=WORKSPACE_ROOT_ID,
            content={"schemaMemory": [
                {"name": "When", "type": "date", "value": {"start": "2045-01-17T09:00"}},
                {"name": "Note", "type": "text", "value": {"text": "2045-01-17T09:00"}},
            ]},
        )
        db.commit()
        yield {"winter": winter.id, "summer": summer.id, "when": when.id,
               "note": note.id, "moved": moved.id}


def _value(page_id, schema_id):
    with db_module.SessionLocal() as db:
        return db.query(PropertyValue).filter_by(page_id=page_id, property_schema_id=schema_id).one().value


def test_winter_and_summer_values_get_their_own_offset(scene):
    with db_module.SessionLocal() as db:
        report = script.convert(db, BERLIN)

    assert report.values == 2 and report.snapshots == 1
    assert _value(scene["winter"], scene["when"]) == {
        "start": "2045-01-17T08:00:00Z", "end": "2045-01-17T09:00:00Z",
    }
    summer = _value(scene["summer"], scene["when"])
    assert summer["start"] == "2045-08-17T07:00:00Z"
    assert summer["repeat"] == "none"
    assert summer["_timeline"] == {"2045→": {"start": "2045-08-18T07:00:00Z"}}


def test_other_properties_and_snapshot_texts_stay(scene):
    with db_module.SessionLocal() as db:
        script.convert(db, BERLIN)
        moved = db.get(Block, scene["moved"])
        memory = moved.content["schemaMemory"]

    assert _value(scene["winter"], scene["note"]) == {"text": "2045-01-17T09:00"}
    assert memory[0]["value"] == {"start": "2045-01-17T08:00:00Z"}
    assert memory[1]["value"] == {"text": "2045-01-17T09:00"}


def test_a_second_run_changes_nothing(scene):
    with db_module.SessionLocal() as db:
        script.convert(db, BERLIN)
    with db_module.SessionLocal() as db:
        report = script.convert(db, BERLIN)
    assert report.values == 0 and report.snapshots == 0
    assert _value(scene["winter"], scene["when"])["start"] == "2045-01-17T08:00:00Z"


def test_a_dry_run_only_counts(scene):
    with db_module.SessionLocal() as db:
        report = script.convert(db, BERLIN, dry_run=True)
    assert report.values == 2
    assert _value(scene["winter"], scene["when"])["start"] == "2045-01-17T09:00"


def test_main_refuses_an_unknown_zone(capsys):
    assert script.main(["--timezone", "Not/AZone", "--dry-run"]) == 2
    assert "Unknown time zone" in capsys.readouterr().err


def test_main_runs_with_the_given_zone(scene, capsys):
    assert script.main(["--timezone", "Europe/Berlin"]) == 0
    assert "Europe/Berlin" in capsys.readouterr().out
    assert _value(scene["winter"], scene["when"])["start"] == "2045-01-17T08:00:00Z"
