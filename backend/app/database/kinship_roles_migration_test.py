"""
Tests for revision 0035, which marks the existing properties of the family
set.

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

_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0035_kinship_roles.py"
_spec = importlib.util.spec_from_file_location("migration_0035", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

DB_A = str(uuid.uuid4())


def _row(name, type_, config, position=1.0, database_id=DB_A):
    return {
        "id": str(uuid.uuid4()), "database_id": database_id, "name": name,
        "type": type_, "config": config, "position": position,
    }


# ─── Rules ───────────────────────────────────────────────────────────────────


def test_the_migration_knew_the_names_of_the_application_at_its_time():
    # Revision 0036 removed half-siblings from the application's names.
    current = {r: set(n.values()) for r, n in kinship.RESERVED_NAMES.items()}
    assert {r: set(n) for r, n in migration.RESERVED_NAMES.items()} == {
        **current, "half_siblings": {"Halbgeschwister", "Half-siblings"},
    }
    assert migration.ROLE_KEY == kinship.ROLE_KEY
    assert migration.FEMALE_OPTION_KEY == kinship.FEMALE_OPTION_KEY


def test_assign_roles_marks_fitting_properties_once_per_database():
    parents = _row("Eltern", "relation", {
        "target_database_id": DB_A, "direction": "bilateral", "mirror_property_name": "Kinder",
    })
    later = _row("Parents", "relation", {
        "target_database_id": DB_A, "direction": "bilateral", "mirror_property_name": "Children",
    }, position=5.0)
    unfit = _row("Geschwister", "relation", {"target_database_id": str(uuid.uuid4()), "direction": "bilateral_self"})
    birth = _row("geburtsdatum ", "rollup", {})
    other_db = _row("Partner", "relation", {"target_database_id": "x", "direction": "bilateral_self"}, database_id="x")

    roles = migration.assign_roles([later, parents, unfit, birth, other_db])

    assert roles == {
        parents["id"]: "parents", birth["id"]: "birth", other_db["id"]: "partners",
    }


def test_marked_config_points_at_the_female_option_and_drops_own_nuances():
    config, removed = migration.marked_config(
        {"options": [{"id": "o-1", "label": "Male"}, {"id": "o-2", "label": "Weiblich"}]}, "sex",
    )
    assert config[migration.FEMALE_OPTION_KEY] == "o-2"
    assert removed == set()

    config, removed = migration.marked_config(
        {"nuance": {"enabled": True, "options": [{"id": "n-1", "label": "Label A"}]}}, "partners",
    )
    assert config == {"kinRole": "partners", "nuance": {"enabled": True, "options": []}}
    assert removed == {"n-1"}


# ─── Against the database ────────────────────────────────────────────────────


@pytest.fixture
def connection(test_engine):
    with test_engine.begin() as conn:
        yield conn


def _block(connection, type_):
    block_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position, state) VALUES (:id, :type, 1.0, 'active')"
    ), {"id": block_id, "type": type_})
    return block_id


def _schema(connection, database_id, name, type_, config, position=1.0):
    schema_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO property_schemas (id, database_id, name, type, position, config, \"group\") "
        "VALUES (:id, :db, :name, :type, :position, CAST(:config AS jsonb), 'Standard')"
    ), {"id": schema_id, "db": database_id, "name": name, "type": type_,
        "position": position, "config": json.dumps(config)})
    return schema_id


def _value(connection, page_id, schema_id, value):
    value_id = uuid.uuid4()
    connection.execute(sa.text(
        "INSERT INTO property_values (id, page_id, property_schema_id, value) "
        "VALUES (:id, :page, :schema, CAST(:value AS jsonb))"
    ), {"id": value_id, "page": page_id, "schema": schema_id, "value": json.dumps(value)})
    return value_id


def _read(connection, table, column, row_id):
    return connection.scalar(sa.text(f"SELECT {column} FROM {table} WHERE id = :id"), {"id": row_id})


def test_migrate_marks_properties_and_strips_their_own_nuances(connection):
    database = _block(connection, "database")
    entry = _block(connection, "page")
    partners = _schema(connection, database, "Partners", "relation", {
        "target_database_id": str(database), "direction": "bilateral_self",
        "nuance": {"enabled": True, "options": [{"id": "n-1", "label": "Label A"}]},
    })
    text = _schema(connection, database, "Kinder", "text", {})
    sex = _schema(connection, database, "Sex (biological)", "select", {
        "options": [{"id": "o-1", "label": "female"}],
    })
    value = _value(connection, entry, partners, {
        "related_ids": [str(entry)], "nuances": {str(entry): "n-1"},
    })

    roles = migration.migrate(connection)

    assert roles == {str(partners): "partners", str(sex): "sex"}
    assert _read(connection, "property_schemas", "config", partners)["nuance"]["options"] == []
    assert _read(connection, "property_schemas", "config", text) == {}
    assert _read(connection, "property_schemas", "config", sex)["kinFemaleOptionId"] == "o-1"
    assert _read(connection, "property_values", "value", value) == {"related_ids": [str(entry)]}
