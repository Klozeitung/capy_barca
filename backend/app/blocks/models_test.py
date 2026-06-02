"""
Tests for the Block, PropertySchema, and PropertyValue models.

All tests run against the isolated in-memory SQLite database provided by the
autouse ``isolated_db`` fixture in conftest.py. The SQLite engine does not
support PostgreSQL-specific features (JSONB, TIMESTAMPTZ, triggers), but
correctly exercises model structure, ORM behaviour, relationships, and
constraint enforcement.

Trigger behaviour (auto-updating updated_at on UPDATE) is a PostgreSQL
concern and is verified by the integration test suite running against a live
container.
"""
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect

import app.session.session as s
from app.blocks.models import (
    WORKSPACE_ROOT_ID,
    Block,
    PropertySchema,
    PropertyValue,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """Yield a live ORM session connected to the isolated test database."""
    with s.SessionLocal() as session:
        yield session


@pytest.fixture
def workspace(db):
    """Insert and return a workspace root block."""
    block = Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0)
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


@pytest.fixture
def page(db, workspace):
    """Insert and return a page block parented to the workspace."""
    block = Block(type="page", position=1.0, parent_id=workspace.id)
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


@pytest.fixture
def database_block(db, workspace):
    """Insert and return a database block parented to the workspace."""
    block = Block(type="database", position=2.0, parent_id=workspace.id)
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


# ─── Table structure ─────────────────────────────────────────────────────────


def test_blocks_table_name():
    assert Block.__tablename__ == "blocks"


def test_property_schemas_table_name():
    assert PropertySchema.__tablename__ == "property_schemas"


def test_property_values_table_name():
    assert PropertyValue.__tablename__ == "property_values"


def test_blocks_table_exists_in_db(db):
    table_names = inspect(db.bind).get_table_names()
    assert "blocks" in table_names


def test_property_schemas_table_exists_in_db(db):
    table_names = inspect(db.bind).get_table_names()
    assert "property_schemas" in table_names


def test_property_values_table_exists_in_db(db):
    table_names = inspect(db.bind).get_table_names()
    assert "property_values" in table_names


# ─── Block creation and defaults ─────────────────────────────────────────────


def test_block_id_is_auto_generated(db):
    block = Block(type="page", position=1.0)
    db.add(block)
    db.commit()
    assert block.id is not None
    assert isinstance(block.id, uuid.UUID)


def test_block_state_defaults_to_active(db):
    block = Block(type="page", position=1.0)
    db.add(block)
    db.commit()
    db.refresh(block)
    assert block.state == "active"


def test_block_parent_id_is_nullable(db):
    block = Block(type="workspace", position=0.0)
    db.add(block)
    db.commit()
    db.refresh(block)
    assert block.parent_id is None


def test_block_reference_id_is_nullable(db):
    block = Block(type="page", position=1.0)
    db.add(block)
    db.commit()
    db.refresh(block)
    assert block.reference_id is None


def test_block_content_is_nullable(db):
    block = Block(type="page", position=1.0)
    db.add(block)
    db.commit()
    db.refresh(block)
    assert block.content is None


def test_block_content_stores_json(db):
    content = {"text": [{"plain_text": "Hello World", "annotations": {"bold": False}}]}
    block = Block(type="paragraph", position=1.0, content=content)
    db.add(block)
    db.commit()
    db.refresh(block)
    assert block.content == content


def test_workspace_root_id_is_stable():
    assert WORKSPACE_ROOT_ID == uuid.UUID("00000000-0000-0000-0000-000000000001")


def test_workspace_block_uses_root_id(workspace):
    assert workspace.id == WORKSPACE_ROOT_ID


# ─── Block parent/child relationship ─────────────────────────────────────────


def test_block_parent_relationship(db, page, workspace):
    db.refresh(page)
    assert page.parent_id == workspace.id


def test_block_children_relationship(db, workspace, page):
    db.refresh(workspace)
    child_ids = [c.id for c in workspace.children]
    assert page.id in child_ids


def test_multiple_children_under_same_parent(db, workspace):
    child_a = Block(type="page", position=1.0, parent_id=workspace.id)
    child_b = Block(type="page", position=2.0, parent_id=workspace.id)
    db.add_all([child_a, child_b])
    db.commit()
    db.refresh(workspace)
    assert len(workspace.children) == 2


def test_block_reference_id_points_to_other_block(db, workspace, database_block):
    view = Block(
        type="database_view",
        position=3.0,
        parent_id=workspace.id,
        reference_id=database_block.id,
    )
    db.add(view)
    db.commit()
    db.refresh(view)
    assert view.reference_id == database_block.id


def test_linked_database_reference_id_points_to_database(db, workspace, database_block):
    """A linked_database block holds reference_id → target database block."""
    linked = Block(
        type="linked_database",
        position=4.0,
        parent_id=workspace.id,
        reference_id=database_block.id,
    )
    db.add(linked)
    db.commit()
    db.refresh(linked)
    assert linked.reference_id == database_block.id
    assert linked.type == "linked_database"


def test_linked_database_reference_id_fk_is_set_null():
    """The reference_id FK is configured with ON DELETE SET NULL at the ORM level.

    The actual runtime cascade (reference_id → NULL when target is deleted) is a
    PostgreSQL-level behaviour and is covered by the integration test suite.
    SQLite does not enforce FK actions without PRAGMA foreign_keys = ON, so we
    verify the SQLAlchemy Column definition directly instead.
    """
    col = Block.__table__.c.reference_id
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "SET NULL"


# ─── PropertySchema ───────────────────────────────────────────────────────────


def test_property_schema_can_be_created(db, database_block):
    schema = PropertySchema(
        database_id=database_block.id,
        name="Status",
        type="select",
        position=1.0,
        config={"options": ["Todo", "Done"]},
    )
    db.add(schema)
    db.commit()
    db.refresh(schema)
    assert schema.id is not None
    assert schema.name == "Status"


def test_property_schema_linked_to_database_block(db, database_block):
    schema = PropertySchema(
        database_id=database_block.id, name="Tags", type="multiselect", position=1.0
    )
    db.add(schema)
    db.commit()
    db.refresh(database_block)
    assert any(s.name == "Tags" for s in database_block.property_schemas)


def test_property_schema_name_unique_per_database(db, database_block):
    schema_a = PropertySchema(
        database_id=database_block.id, name="Priority", type="select", position=1.0
    )
    schema_b = PropertySchema(
        database_id=database_block.id, name="Priority", type="select", position=2.0
    )
    db.add(schema_a)
    db.commit()
    db.add(schema_b)
    with pytest.raises(Exception):
        db.commit()


def test_property_schema_same_name_allowed_in_different_databases(db, workspace):
    db_a = Block(type="database", position=1.0, parent_id=workspace.id)
    db_b = Block(type="database", position=2.0, parent_id=workspace.id)
    db.add_all([db_a, db_b])
    db.commit()

    schema_a = PropertySchema(
        database_id=db_a.id, name="Status", type="select", position=1.0
    )
    schema_b = PropertySchema(
        database_id=db_b.id, name="Status", type="select", position=1.0
    )
    db.add_all([schema_a, schema_b])
    db.commit()
    assert schema_a.id != schema_b.id


# ─── PropertyValue ────────────────────────────────────────────────────────────


def test_property_value_can_be_created(db, page, database_block):
    schema = PropertySchema(
        database_id=database_block.id, name="Done", type="checkbox", position=1.0
    )
    db.add(schema)
    db.commit()

    pv = PropertyValue(
        page_id=page.id,
        property_schema_id=schema.id,
        value={"checked": True},
    )
    db.add(pv)
    db.commit()
    db.refresh(pv)
    assert pv.id is not None
    assert pv.value == {"checked": True}


def test_property_value_unique_per_page_and_schema(db, page, database_block):
    schema = PropertySchema(
        database_id=database_block.id, name="Title", type="text", position=1.0
    )
    db.add(schema)
    db.commit()

    pv_a = PropertyValue(page_id=page.id, property_schema_id=schema.id, value={"text": "A"})
    pv_b = PropertyValue(page_id=page.id, property_schema_id=schema.id, value={"text": "B"})
    db.add(pv_a)
    db.commit()
    db.add(pv_b)
    with pytest.raises(Exception):
        db.commit()


def test_property_value_nullable_value(db, page, database_block):
    schema = PropertySchema(
        database_id=database_block.id, name="Notes", type="text", position=1.0
    )
    db.add(schema)
    db.commit()

    pv = PropertyValue(page_id=page.id, property_schema_id=schema.id, value=None)
    db.add(pv)
    db.commit()
    db.refresh(pv)
    assert pv.value is None


def test_property_schema_group_defaults_to_standard(db, database_block):
    schema = PropertySchema(
        database_id=database_block.id,
        name="GroupDefault",
        type="text",
        position=1.0,
    )
    db.add(schema)
    db.commit()
    db.refresh(schema)
    assert schema.group == "Standard"


def test_property_schema_group_can_be_set_explicitly(db, database_block):
    schema = PropertySchema(
        database_id=database_block.id,
        name="GroupCustom",
        type="text",
        position=2.0,
        group="Kontakt",
    )
    db.add(schema)
    db.commit()
    db.refresh(schema)
    assert schema.group == "Kontakt"


def test_property_value_relationship_to_page(db, page, database_block):
    schema = PropertySchema(
        database_id=database_block.id, name="Due", type="date", position=1.0
    )
    db.add(schema)
    db.commit()

    pv = PropertyValue(
        page_id=page.id,
        property_schema_id=schema.id,
        value={"date": "2026-01-01"},
    )
    db.add(pv)
    db.commit()
    db.refresh(page)
    assert any(v.property_schema_id == schema.id for v in page.property_values)


# ─── Block owner_id ───────────────────────────────────────────────────────────


def test_block_owner_id_is_nullable(db):
    block = Block(type="page", position=1.0)
    db.add(block)
    db.commit()
    db.refresh(block)
    assert block.owner_id is None


def test_block_owner_id_can_be_set(db):
    import uuid as _uuid
    uid = _uuid.uuid4()
    block = Block(type="page", position=1.0, owner_id=uid)
    db.add(block)
    db.commit()
    db.refresh(block)
    assert block.owner_id == uid


def test_block_owner_id_is_independent_of_created_by(db, database_block):
    """owner_id lives on the Block row; created_by is a PropertySchema type."""
    from app.blocks.models import PropertySchema
    import uuid as _uuid
    uid = _uuid.uuid4()
    block = Block(type="page", position=1.0, owner_id=uid)
    db.add(block)
    db.commit()
    db.refresh(block)
    assert block.owner_id == uid
    # The Block row has no 'created_by' column — that is a property schema type.
    assert not hasattr(block, "created_by")
