"""
Tests for the relation locks.

The scope helpers are plain functions. The lock itself is exercised with two
real sessions on two connections, because waiting is the whole point of it.
"""
import threading
import time
import uuid

import pytest

import app.session.session as s
from app.blocks import relation_lock
from app.blocks.computed import SchemaLike
from app.blocks import repository as repo
from app.blocks.models import WORKSPACE_ROOT_ID, Block


def _schema(type_: str, config: dict | None = None) -> SchemaLike:
    return SchemaLike(id=uuid.uuid4(), name="Link", type=type_, config=config)


# ─── Keys ─────────────────────────────────────────────────────────────────────


def test_lock_key_is_a_stable_signed_32_bit_value():
    database_id = uuid.UUID("ffffffff-0000-0000-0000-000000000000")
    assert relation_lock.lock_key(database_id) == -1
    assert relation_lock.lock_key(database_id) == relation_lock.lock_key(database_id)
    other = uuid.UUID("00000001-0000-0000-0000-000000000000")
    assert relation_lock.lock_key(other) == 1


# ─── Scope of a write ─────────────────────────────────────────────────────────


def test_scope_of_a_bilateral_relation_includes_its_target():
    own, target = uuid.uuid4(), uuid.uuid4()
    schema = _schema("relation", {"direction": "bilateral", "target_database_id": str(target)})
    assert relation_lock.databases_for_schema(own, schema) == {own, target}


def test_scope_of_relations_that_stay_in_their_database():
    own, target = uuid.uuid4(), uuid.uuid4()
    unilateral = _schema("relation", {"direction": "unilateral", "target_database_id": str(target)})
    self_mirrored = _schema("relation", {"direction": "bilateral_self"})
    assert relation_lock.databases_for_schema(own, unilateral) == {own}
    assert relation_lock.databases_for_schema(own, self_mirrored) == {own}
    assert relation_lock.databases_for_schema(own, _schema("parent_item", {})) == {own}
    assert relation_lock.databases_for_schema(own, _schema("sub_item", None)) == {own}


def test_scope_ignores_a_malformed_target():
    own = uuid.uuid4()
    schema = _schema("relation", {"direction": "bilateral", "target_database_id": "not-a-uuid"})
    assert relation_lock.databases_for_schema(own, schema) == {own}


@pytest.fixture
def db():
    with s.SessionLocal() as session:
        yield session


@pytest.fixture
def databases(db):
    db.add(Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0))
    db.commit()
    own = repo.create_block(db, type="database", position=1.0, parent_id=WORKSPACE_ROOT_ID)
    first = repo.create_block(db, type="database", position=2.0, parent_id=WORKSPACE_ROOT_ID)
    second = repo.create_block(db, type="database", position=3.0, parent_id=WORKSPACE_ROOT_ID)
    db.commit()
    return own.id, first.id, second.id


def test_scope_of_a_database_covers_all_its_bilateral_targets(db, databases):
    own, first, second = databases
    repo.create_schema(
        db, database_id=own, name="To first", type="relation", position=1.0,
        config={"direction": "bilateral", "target_database_id": str(first)},
    )
    repo.create_schema(
        db, database_id=own, name="To second", type="relation", position=2.0,
        config={"direction": "unilateral", "target_database_id": str(second)},
    )
    repo.create_schema(db, database_id=own, name="Notes", type="text", position=3.0)
    db.commit()

    assert relation_lock.databases_for_database(db, own) == {own, first}


# ─── Taking the locks ─────────────────────────────────────────────────────────


def test_locks_are_taken_in_ascending_key_order(db):
    ids = [uuid.uuid4() for _ in range(5)]
    keys = relation_lock.lock_relation_databases(db, ids)
    db.rollback()

    assert keys == sorted(relation_lock.lock_key(i) for i in ids)


def test_nothing_to_lock_runs_no_statement(db, count_queries):
    with count_queries() as counter:
        assert relation_lock.lock_relation_databases(db, []) == []
    assert counter.count == 0


def test_a_second_transaction_waits_until_the_first_one_ends(db):
    database_id = uuid.uuid4()
    relation_lock.lock_relation_databases(db, [database_id])

    acquired = threading.Event()
    failures: list[BaseException] = []

    def contender() -> None:
        try:
            with s.SessionLocal() as other:
                relation_lock.lock_relation_databases(other, [database_id])
                acquired.set()
                other.rollback()
        except BaseException as exc:  # surfaced in the main thread below
            failures.append(exc)
            acquired.set()

    thread = threading.Thread(target=contender)
    thread.start()
    time.sleep(0.3)
    assert not acquired.is_set()

    db.commit()
    thread.join(timeout=5)

    assert not failures
    assert acquired.is_set()


def test_other_databases_do_not_wait(db):
    relation_lock.lock_relation_databases(db, [uuid.UUID(int=1 << 96)])

    acquired = threading.Event()

    def contender() -> None:
        with s.SessionLocal() as other:
            relation_lock.lock_relation_databases(other, [uuid.UUID(int=2 << 96)])
            acquired.set()
            other.rollback()

    thread = threading.Thread(target=contender)
    thread.start()
    thread.join(timeout=5)
    db.rollback()

    assert acquired.is_set()
