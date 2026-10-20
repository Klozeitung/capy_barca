"""
Tests for revision 0026, which rederives relation timelines at minute
resolution.

The derivation is tested as pure functions; ``migrate`` runs against the
PostgreSQL test database, whose schema the session fixture has already
migrated to head, inside a transaction that is always rolled back.
"""
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0026_relation_timeline_minutes.py"
)
_spec = importlib.util.spec_from_file_location("migration_0026", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

# Two links whose ranges hand over in consecutive minutes.
POOL = {
    "a": ["2000-07-28T08:00:00→2010-03-01T14:14:00", "2010-03-01T14:15:00→"],
    "b": ["2000-07-28T08:00:00→2045-07-17T18:05:00"],
}

# What the second-based derivation stored for POOL.
STALE_TIMELINE = {
    "2000-07-28T08:00:00→2010-03-01T14:14:00": {"related_ids": ["a", "b"]},
    "2010-03-01T14:14:01→2010-03-01T14:14:59": {"related_ids": ["b"]},
    "2010-03-01T14:15:00→2045-07-17T18:05:00": {"related_ids": ["a", "b"]},
    "2045-07-17T18:05:01→": {"related_ids": ["a"]},
}

FIXED_TIMELINE = {
    "2000-07-28T08:00:00→2010-03-01T14:14:00": {"related_ids": ["a", "b"]},
    "2010-03-01T14:15:00→2045-07-17T18:05:00": {"related_ids": ["a", "b"]},
    "2045-07-17T18:06:00→": {"related_ids": ["a"]},
}


# ─── Derivation ──────────────────────────────────────────────────────────────


def test_revision_follows_the_database_settings_revision():
    assert migration.revision == "z6c8b7x90026"
    assert migration.down_revision == "y5b7a6w80025"


def test_consecutive_minutes_leave_no_gap():
    assert migration.pool_to_timeline(POOL) == FIXED_TIMELINE


def test_always_valid_links_fill_every_slot_and_the_leading_one():
    pool = {"a": [""], "b": ["2000-07-28T10:00:00→2000-07-28T14:14:00"]}
    assert migration.pool_to_timeline(pool, {"a": {"": "label-a"}}) == {
        "→2000-07-28T09:59:00": {"related_ids": ["a"], "nuances": {"a": "label-a"}},
        "2000-07-28T10:00:00→2000-07-28T14:14:00": {
            "related_ids": ["a", "b"], "nuances": {"a": "label-a"},
        },
        "2000-07-28T14:15:00→": {"related_ids": ["a"], "nuances": {"a": "label-a"}},
    }


def test_only_always_valid_links_form_the_single_empty_slot():
    assert migration.pool_to_timeline({"a": [""], "b": [""]}) == {
        "": {"related_ids": ["a", "b"]},
    }


def test_nuances_follow_the_range_that_makes_a_link_active():
    pool = {"a": ["2000-01-01T00:00:00→2000-12-31T23:59:00", "2001-01-01T00:00:00→"]}
    nuance_pool = {"a": {
        "2000-01-01T00:00:00→2000-12-31T23:59:00": "first",
        "2001-01-01T00:00:00→": "second",
    }}
    assert migration.pool_to_timeline(pool, nuance_pool) == {
        "2000-01-01T00:00:00→2000-12-31T23:59:00": {
            "related_ids": ["a"], "nuances": {"a": "first"},
        },
        "2001-01-01T00:00:00→": {"related_ids": ["a"], "nuances": {"a": "second"}},
    }


def test_until_ranges_and_unusable_ranges():
    pool = {
        "a": ["→2000-07-28T09:59:00"],
        "b": ["2000-07-28T10:00:00→"],
        "c": ["not a date→", "2001-01-01T00:00:00→2000-01-01T00:00:00"],
    }
    assert migration.pool_to_timeline(pool) == {
        "→2000-07-28T09:59:00": {"related_ids": ["a"]},
        "2000-07-28T10:00:00→": {"related_ids": ["b"]},
    }


@pytest.mark.parametrize("value", [
    None,
    {"related_ids": ["a"]},
    {"_timeline": {"": {"related_ids": ["a"]}}},
    "not a value",
])
def test_values_without_a_pool_are_left_alone(value):
    assert migration.recompute_value(value) == value


def test_recompute_keeps_the_pools_and_replaces_the_timeline():
    value = {
        "relationPool": POOL,
        "nuancePool": {},
        "_timeline": STALE_TIMELINE,
    }
    assert migration.recompute_value(value) == {
        "relationPool": POOL,
        "nuancePool": {},
        "_timeline": FIXED_TIMELINE,
    }


# ─── Against the database ────────────────────────────────────────────────────


@pytest.fixture
def connection(test_engine):
    """A connection inside a transaction that is always rolled back."""
    with test_engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


def _block(connection, type_):
    block_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position, state) VALUES (:id, :type, 1.0, 'active')"
    ), {"id": block_id, "type": type_})
    return block_id


def _schema(connection, database_id, type_, config):
    schema_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO property_schemas (id, database_id, name, type, position, config, \"group\") "
        "VALUES (:id, :db, :name, :type, 1.0, CAST(:config AS jsonb), 'Standard')"
    ), {
        "id": schema_id, "db": database_id, "name": f"Property {schema_id.hex[:6]}",
        "type": type_, "config": json.dumps(config),
    })
    return schema_id


def _value(connection, schema_id, value):
    value_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO property_values (id, page_id, property_schema_id, value) "
        "VALUES (:id, :page, :schema, CAST(:value AS jsonb))"
    ), {
        "id": value_id, "page": _block(connection, "page"), "schema": schema_id,
        "value": json.dumps(value),
    })
    return value_id


def _read(connection, value_id):
    return connection.scalar(
        sa.text("SELECT value FROM property_values WHERE id = :id"), {"id": value_id},
    )


def test_migrate_rederives_stale_relation_timelines_only(connection):
    database = _block(connection, "database")
    relation = _schema(connection, database, "relation", {
        "target_database_id": str(database), "direction": "unilateral", "hasTimeline": True,
    })
    text = _schema(connection, database, "text", {"hasTimeline": True})
    stale = _value(connection, relation, {"relationPool": POOL, "_timeline": STALE_TIMELINE})
    current = _value(connection, relation, {"relationPool": POOL, "_timeline": FIXED_TIMELINE})
    flat = _value(connection, relation, {"related_ids": ["a"]})
    # A scalar timeline whose keys look like the stale ones must not be touched.
    scalar_value = {"_timeline": {"2010-03-01T14:14:01→": {"text": "x"}}}
    scalar = _value(connection, text, scalar_value)

    changed = migration.migrate(connection)

    assert changed >= 1
    assert _read(connection, stale) == {"relationPool": POOL, "_timeline": FIXED_TIMELINE}
    assert _read(connection, current) == {"relationPool": POOL, "_timeline": FIXED_TIMELINE}
    assert _read(connection, flat) == {"related_ids": ["a"]}
    assert _read(connection, scalar) == scalar_value


def test_migrate_twice_changes_nothing_the_second_time(connection):
    database = _block(connection, "database")
    relation = _schema(connection, database, "relation", {
        "target_database_id": str(database), "direction": "unilateral", "hasTimeline": True,
    })
    value = _value(connection, relation, {"relationPool": POOL, "_timeline": STALE_TIMELINE})

    migration.migrate(connection)
    first = _read(connection, value)
    migration.migrate(connection)

    assert first["_timeline"] == FIXED_TIMELINE
    assert _read(connection, value) == first
    assert migration.recompute_value(first) == first


def test_downgrade_leaves_the_data_as_it_is(connection):
    database = _block(connection, "database")
    relation = _schema(connection, database, "relation", {
        "target_database_id": str(database), "direction": "unilateral", "hasTimeline": True,
    })
    value = _value(connection, relation, {"relationPool": POOL, "_timeline": FIXED_TIMELINE})

    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.downgrade()

    assert _read(connection, value) == {"relationPool": POOL, "_timeline": FIXED_TIMELINE}
