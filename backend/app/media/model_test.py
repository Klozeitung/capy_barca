"""
Tests for the media-file mapping model.

These run against the model directly rather than through the router, and pin
the two schema decisions that are easy to reverse by accident: the primary key
is the file identifier, and there is deliberately no foreign key to blocks.
"""
import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

import app.database.database as db_module
from app.media.model import MediaFile


def _row(file_uuid=None, block_id=None, category="image", stored_name="x.png"):
    return MediaFile(
        file_uuid=file_uuid or uuid.uuid4(),
        block_id=block_id or uuid.uuid4(),
        category=category,
        stored_name=stored_name,
    )


# ─── Table and columns ────────────────────────────────────────────────────────


def test_table_is_created_by_the_fixture():
    with db_module.SessionLocal() as db:
        assert MediaFile.__tablename__ in inspect(db.bind).get_table_names()


def test_a_row_round_trips():
    file_uuid, block_id = uuid.uuid4(), uuid.uuid4()
    with db_module.SessionLocal() as db:
        db.add(_row(file_uuid, block_id, "image", "cat.png"))
        db.commit()

    with db_module.SessionLocal() as db:
        stored = db.get(MediaFile, file_uuid)
        assert stored.block_id == block_id
        assert stored.category == "image"
        assert stored.stored_name == "cat.png"


def test_created_at_is_populated_without_being_given():
    file_uuid = uuid.uuid4()
    with db_module.SessionLocal() as db:
        db.add(_row(file_uuid))
        db.commit()

    with db_module.SessionLocal() as db:
        assert db.get(MediaFile, file_uuid).created_at is not None


def test_block_id_is_indexed():
    """The delete path looks up by file_uuid, but cleanup work scans by block."""
    with db_module.SessionLocal() as db:
        indexed = {
            column
            for index in inspect(db.bind).get_indexes(MediaFile.__tablename__)
            for column in index["column_names"]
        }
    assert "block_id" in indexed


# ─── Primary key ──────────────────────────────────────────────────────────────


def test_file_uuid_is_the_primary_key():
    """One file, one owner. A second row for the same id must not be possible."""
    file_uuid = uuid.uuid4()
    with db_module.SessionLocal() as db:
        db.add(_row(file_uuid, uuid.uuid4()))
        db.commit()

    with db_module.SessionLocal() as db:
        db.add(_row(file_uuid, uuid.uuid4()))
        with pytest.raises(IntegrityError):
            db.commit()


def test_two_files_may_belong_to_the_same_block():
    block_id = uuid.uuid4()
    with db_module.SessionLocal() as db:
        db.add(_row(uuid.uuid4(), block_id))
        db.add(_row(uuid.uuid4(), block_id))
        db.commit()

    with db_module.SessionLocal() as db:
        assert db.query(MediaFile).filter(MediaFile.block_id == block_id).count() == 2


# ─── Deliberate absence of a foreign key ──────────────────────────────────────


def test_block_id_has_no_foreign_key_constraint():
    """
    Recorded as a decision, not an oversight.

    A constraint would turn an upload naming a block that does not exist into
    an integrity error deep inside the request, where the endpoint answers on
    its own terms today. The row is a record of where a file came from rather
    than a live reference, the same reasoning BlockPermissionGrant.user_id
    carries for users.
    """
    with db_module.SessionLocal() as db:
        keys = inspect(db.bind).get_foreign_keys(MediaFile.__tablename__)
    assert keys == []


def test_a_row_survives_naming_a_block_that_does_not_exist():
    """The behavioural half of the test above."""
    file_uuid = uuid.uuid4()
    with db_module.SessionLocal() as db:
        db.add(_row(file_uuid, uuid.uuid4()))
        db.commit()

    with db_module.SessionLocal() as db:
        assert db.get(MediaFile, file_uuid) is not None
