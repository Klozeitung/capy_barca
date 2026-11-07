"""
Tests for revision 0036, which merges half-siblings into siblings and brings
nuances, timelines, the male marker and the aliases of the family set to the
rules of 0.18.2.0.

The rules are tested as pure functions; ``migrate`` runs against the
PostgreSQL test database, whose schema the migrations have already built.
"""
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.blocks import kinship
from app.blocks.timeline import pool_to_timeline

_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0036_kinship_siblings.py"
_spec = importlib.util.spec_from_file_location("migration_0036", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


# ─── Rules ───────────────────────────────────────────────────────────────────


def test_the_migration_knows_the_same_keys_and_nuances_as_the_application():
    assert migration.MALE_OPTION_KEY == kinship.MALE_OPTION_KEY
    assert set(migration.RELATION_ROLES) == set(kinship.RELATION_ROLES)
    assert set(migration.COUPLED_ROLES) == set(kinship.TIMELINE_COUPLED_ROLES)
    assert {
        n["id"]: (n["de"], n["en"]) for n in kinship.BASE_NUANCES[kinship.GROUP_SIBLINGS]
    } == migration.SIBLING_BASE_LABELS


@pytest.mark.parametrize("pool, nuance_pool", [
    ({"a": [""]}, {"a": {"": "n-1"}}),
    ({"a": ["2000-07-28T00:00:00→2001-01-01T00:00:00"], "b": [""]}, {}),
    ({"a": ["→2010-05-01T12:30:00", "2011-01-01→"]}, {"a": {"2011-01-01→": "n-2"}}),
    ({"a": ["0044-03-15T00:00:00→"]}, {}),
])
def test_the_copied_slot_derivation_matches_the_application(pool, nuance_pool):
    assert migration.pool_to_timeline(pool, nuance_pool) == pool_to_timeline(pool, nuance_pool)


def test_male_option_skips_the_female_option():
    config = {
        "options": [{"id": "o-1", "label": "Male"}, {"id": "o-2", "label": "männlich"}],
        "kinFemaleOptionId": "o-1",
    }
    assert migration.male_option(config) == "o-2"
    assert migration.male_option({"options": [{"id": "o-3", "label": "Other"}]}) is None


def test_timeline_roles_follow_the_tree_and_the_coupling():
    held = {"parents", "children", "partners", "siblings"}
    assert migration.timeline_roles(held, set(), True) == held
    assert migration.timeline_roles(held, {"siblings"}, False) == {"parents", "children", "siblings"}
    assert migration.timeline_roles(held, {"partners"}, False) == set()


def test_to_pool_wraps_flat_values_and_leaves_pools():
    assert migration.to_pool({"related_ids": ["a"], "nuances": {"a": "n-1"}}) == {
        "relationPool": {"a": [""]},
        "nuancePool": {"a": {"": "n-1"}},
        "_timeline": {"": {"related_ids": ["a"], "nuances": {"a": "n-1"}}},
    }
    pooled = {"relationPool": {"a": [""]}, "_timeline": {}}
    assert migration.to_pool(pooled) is pooled


def test_add_link_fills_only_a_missing_nuance():
    flat = migration.add_link({"related_ids": ["a"], "nuances": {"a": "n-1"}}, "a", "", "n-2", False)
    assert flat["nuances"] == {"a": "n-1"}
    flat = migration.add_link(None, "b", "", "n-2", False)
    assert flat == {"related_ids": ["b"], "nuances": {"b": "n-2"}}

    pooled = migration.add_link(None, "b", "2000-07-28→", "n-2", True)
    assert pooled["relationPool"] == {"b": ["2000-07-28→"]}
    assert pooled["nuancePool"] == {"b": {"2000-07-28→": "n-2"}}
    assert pooled["_timeline"] == pool_to_timeline(pooled["relationPool"], pooled["nuancePool"])


def test_merge_aliases_moves_half_siblings_and_merges_label_clashes():
    aliases = [
        {"id": "a-1", "group": "half_siblings", "base": None, "label": "Label A"},
        {"id": "a-2", "group": "siblings", "base": None, "label": "Label B"},
        {"id": "a-3", "group": "half_siblings", "base": None, "label": "label b"},
        {"id": "a-4", "group": "half_siblings", "base": None, "label": "Half"},
        {"id": "a-5", "group": "parents", "base": "kin.parent.legal", "label": "Label C"},
    ]
    result, merged = migration.merge_aliases(aliases)
    assert result == [
        {"id": "a-1", "group": "siblings", "base": "kin.sibling.half", "label": "Label A"},
        {"id": "a-2", "group": "siblings", "base": "kin.sibling.biological", "label": "Label B"},
        {"id": "a-5", "group": "parents", "base": "kin.parent.legal", "label": "Label C"},
    ]
    assert merged == {"a-3": "a-2", "a-4": "kin.sibling.half"}
    assert kinship.normalize_aliases(result, result) == result


# ─── Against the database ────────────────────────────────────────────────────


@pytest.fixture
def connection(test_engine):
    with test_engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM app_settings WHERE key = 'kinship_nuances'"))
        yield conn


def _block(connection, type_):
    block_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position, state) VALUES (:id, :type, 1.0, 'active')"
    ), {"id": block_id, "type": type_})
    return block_id


def _schema(connection, database_id, name, config):
    schema_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO property_schemas (id, database_id, name, type, position, config, \"group\") "
        "VALUES (:id, :db, :name, :type, 1.0, CAST(:config AS jsonb), 'Standard')"
    ), {"id": schema_id, "db": database_id, "name": name,
        "type": "select" if config.get("kinRole") == "sex" else "relation",
        "config": json.dumps(config)})
    return schema_id


def _value(connection, page_id, schema_id, value):
    connection.execute(sa.text(
        "INSERT INTO property_values (id, page_id, property_schema_id, value) "
        "VALUES (:id, :page, :schema, CAST(:value AS jsonb))"
    ), {"id": uuid.uuid4(), "page": page_id, "schema": schema_id, "value": json.dumps(value)})


def _config(connection, schema_id):
    return connection.scalar(sa.text("SELECT config FROM property_schemas WHERE id = :id"), {"id": schema_id})


def _stored(connection, page_id, schema_id):
    return connection.scalar(sa.text(
        "SELECT value FROM property_values WHERE page_id = :page AND property_schema_id = :schema"
    ), {"page": page_id, "schema": schema_id})


def _self(database, role, **extra):
    return {"target_database_id": str(database), "direction": "bilateral_self", "kinRole": role, **extra}


def test_migrate_moves_half_siblings_into_siblings_on_both_sides(connection):
    connection.execute(sa.text(
        "INSERT INTO app_settings (key, value) VALUES ('kinship_nuances', CAST(:value AS jsonb))"
    ), {"value": json.dumps({"aliases": [
        {"id": "a-1", "group": "half_siblings", "base": None, "label": "Label A"},
    ]})})
    database = _block(connection, "database")
    a, b, c = (_block(connection, "page") for _ in range(3))
    siblings = _schema(connection, database, "Siblings", _self(database, "siblings"))
    half = _schema(connection, database, "Half-siblings", _self(database, "half_siblings"))
    partners = _schema(connection, database, "Partners", _self(database, "partners", nuance={"enabled": False}))
    sex = _schema(connection, database, "Sex (biological)", {
        "kinRole": "sex", "kinFemaleOptionId": "o-1",
        "options": [{"id": "o-1", "label": "female"}, {"id": "o-2", "label": "male"}],
    })
    _value(connection, a, half, {"related_ids": [str(b), str(c)], "nuances": {str(c): "a-1"}})
    _value(connection, a, siblings, {"related_ids": [str(b)], "nuances": {str(b): "kin.sibling.step"}})

    summary = migration.migrate(connection)

    assert summary["unmarked"] == [str(half)]
    assert "kinRole" not in _config(connection, half)
    assert _config(connection, partners)["nuance"]["enabled"] is True
    assert _config(connection, siblings)["nuance"]["enabled"] is True
    assert _config(connection, sex)["kinMaleOptionId"] == "o-2"
    assert _stored(connection, a, siblings) == {
        "related_ids": [str(b), str(c)],
        "nuances": {str(b): "kin.sibling.step", str(c): "a-1"},
    }
    assert _stored(connection, b, siblings) == {
        "related_ids": [str(a)], "nuances": {str(a): "kin.sibling.half"},
    }
    assert _stored(connection, c, siblings) == {"related_ids": [str(a)], "nuances": {str(a): "a-1"}}
    stored = connection.scalar(sa.text("SELECT value FROM app_settings WHERE key = 'kinship_nuances'"))
    assert stored["aliases"] == [
        {"id": "a-1", "group": "siblings", "base": "kin.sibling.half", "label": "Label A"},
    ]


def test_migrate_couples_timelines_and_keeps_ranges_of_half_siblings(connection):
    database = _block(connection, "database")
    a, b = (_block(connection, "page") for _ in range(2))
    parents = _schema(connection, database, "Parents", {
        "target_database_id": str(database), "direction": "bilateral",
        "mirror_property_name": "Children", "kinRole": "parents", "hasTimeline": True,
    })
    siblings = _schema(connection, database, "Siblings", _self(database, "siblings"))
    half = _schema(connection, database, "Half-siblings", _self(database, "half_siblings"))
    _value(connection, a, siblings, {"related_ids": [str(b)]})
    _value(connection, a, half, {"relationPool": {str(b): ["2000-07-28T00:00:00→"]}})

    summary = migration.migrate(connection)

    assert str(siblings) in summary["timelined"]
    assert str(parents) not in summary["timelined"]
    assert _config(connection, siblings)["hasTimeline"] is True
    stored = _stored(connection, a, siblings)
    assert stored["relationPool"] == {str(b): ["", "2000-07-28T00:00:00→"]}
    assert stored["nuancePool"] == {str(b): {"2000-07-28T00:00:00→": "kin.sibling.half"}}
    assert _stored(connection, b, siblings)["relationPool"] == {str(a): ["2000-07-28T00:00:00→"]}
    assert stored["_timeline"] == pool_to_timeline(stored["relationPool"], stored["nuancePool"])


def test_migrate_gives_every_relation_role_a_timeline_under_a_family_tree(connection):
    database = _block(connection, "database")
    connection.execute(sa.text(
        "INSERT INTO database_views (id, database_id, name, view_type, position, filter_groups, sorts, settings) "
        "VALUES (:id, :db, 'Tree', 'family_tree', 1.0, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb)"
    ), {"id": uuid.uuid4(), "db": database})
    partners = _schema(connection, database, "Partners", _self(database, "partners"))

    migration.migrate(connection)

    assert _config(connection, partners)["hasTimeline"] is True
