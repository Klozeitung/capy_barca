"""
Tests for the block repository.

All tests run against the isolated in-memory SQLite database provided by
the autouse ``isolated_db`` fixture in conftest.py. The repository is
exercised via an ORM session; no HTTP layer is involved.
"""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

import app.session.session as s
from app.blocks.models import WORKSPACE_ROOT_ID, Block, PropertySchema
from app.blocks import repository as repo


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    with s.SessionLocal() as session:
        yield session


@pytest.fixture
def workspace(db):
    block = Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0)
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


@pytest.fixture
def page(db, workspace):
    block = repo.create_block(db, type="page", position=1.0, parent_id=workspace.id)
    db.commit()
    return block


@pytest.fixture
def database_block(db, workspace):
    block = repo.create_block(db, type="database", position=2.0, parent_id=workspace.id)
    db.commit()
    return block


@pytest.fixture
def schema(db, database_block):
    s_ = repo.create_schema(
        db,
        database_id=database_block.id,
        name="Status",
        type="select",
        position=1.0,
        config={"options": ["Todo", "Done"]},
    )
    db.commit()
    return s_


# ─── get_block ────────────────────────────────────────────────────────────────


def test_get_block_returns_existing_block(db, workspace):
    result = repo.get_block(db, workspace.id)
    assert result is not None
    assert result.id == workspace.id


def test_get_block_returns_none_for_unknown_id(db):
    result = repo.get_block(db, uuid.uuid4())
    assert result is None


def test_get_block_or_raise_returns_block(db, workspace):
    result = repo.get_block_or_raise(db, workspace.id)
    assert result.id == workspace.id


def test_get_block_or_raise_raises_for_unknown_id(db):
    with pytest.raises(KeyError):
        repo.get_block_or_raise(db, uuid.uuid4())


# ─── list_children ────────────────────────────────────────────────────────────


def test_list_children_returns_active_children(db, workspace, page):
    children = repo.list_children(db, workspace.id)
    assert any(c.id == page.id for c in children)


def test_list_children_ordered_by_position(db, workspace):
    b1 = repo.create_block(db, type="page", position=3.0, parent_id=workspace.id)
    b2 = repo.create_block(db, type="page", position=1.0, parent_id=workspace.id)
    b3 = repo.create_block(db, type="page", position=2.0, parent_id=workspace.id)
    db.commit()
    children = repo.list_children(db, workspace.id)
    positions = [c.position for c in children]
    assert positions == sorted(positions)


def test_list_children_excludes_trash_by_default(db, workspace):
    trashed = repo.create_block(
        db, type="page", position=5.0, parent_id=workspace.id, state="trash"
    )
    db.commit()
    children = repo.list_children(db, workspace.id)
    assert all(c.id != trashed.id for c in children)


def test_list_children_includes_trash_when_state_is_none(db, workspace):
    trashed = repo.create_block(
        db, type="page", position=5.0, parent_id=workspace.id, state="trash"
    )
    db.commit()
    children = repo.list_children(db, workspace.id, state=None)
    assert any(c.id == trashed.id for c in children)


def test_list_children_returns_empty_for_childless_block(db, page):
    children = repo.list_children(db, page.id)
    assert children == []


def test_list_children_exclude_types_filters_out_entry_template(db, workspace, database_block):
    """entry_template blocks are hidden from regular entry listings."""
    template = repo.create_block(
        db, type="entry_template", position=1.0, parent_id=database_block.id
    )
    page_entry = repo.create_block(
        db, type="page", position=2.0, parent_id=database_block.id
    )
    db.commit()

    all_children = repo.list_children(db, database_block.id)
    assert any(c.id == template.id for c in all_children)
    assert any(c.id == page_entry.id for c in all_children)

    filtered = repo.list_children(
        db, database_block.id, exclude_types=frozenset({"entry_template"})
    )
    assert all(c.id != template.id for c in filtered)
    assert any(c.id == page_entry.id for c in filtered)


def test_list_children_exclude_types_none_returns_all(db, workspace, database_block):
    """Passing exclude_types=None (default) does not filter any types."""
    template = repo.create_block(
        db, type="entry_template", position=1.0, parent_id=database_block.id
    )
    db.commit()
    children = repo.list_children(db, database_block.id, exclude_types=None)
    assert any(c.id == template.id for c in children)


# ─── list_blocks_by_ids ───────────────────────────────────────────────────────


def test_list_blocks_by_ids_returns_requested_blocks(db, workspace, database_block):
    b1 = repo.create_block(db, type="page", position=1.0, parent_id=database_block.id)
    b2 = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    db.commit()
    result = repo.list_blocks_by_ids(db, [b1.id, b2.id])
    ids = {b.id for b in result}
    assert ids == {b1.id, b2.id}


def test_list_blocks_by_ids_empty_input_returns_empty(db):
    assert repo.list_blocks_by_ids(db, []) == []


def test_list_blocks_by_ids_omits_unknown_ids(db, database_block):
    b1 = repo.create_block(db, type="page", position=1.0, parent_id=database_block.id)
    db.commit()
    result = repo.list_blocks_by_ids(db, [b1.id, uuid.uuid4()])
    assert [b.id for b in result] == [b1.id]


def test_list_blocks_by_ids_filters_by_parent(db, workspace, database_block):
    """parent_id restricts the result to direct children of that block."""
    inside = repo.create_block(db, type="page", position=1.0, parent_id=database_block.id)
    outside = repo.create_block(db, type="page", position=2.0, parent_id=workspace.id)
    db.commit()
    result = repo.list_blocks_by_ids(
        db, [inside.id, outside.id], parent_id=database_block.id
    )
    assert [b.id for b in result] == [inside.id]


def test_list_blocks_by_ids_excludes_trash_by_default(db, database_block):
    active = repo.create_block(db, type="page", position=1.0, parent_id=database_block.id)
    trashed = repo.create_block(
        db, type="page", position=2.0, parent_id=database_block.id, state="trash"
    )
    db.commit()
    result = repo.list_blocks_by_ids(db, [active.id, trashed.id])
    assert [b.id for b in result] == [active.id]


def test_list_blocks_by_ids_includes_trash_when_state_is_none(db, database_block):
    trashed = repo.create_block(
        db, type="page", position=1.0, parent_id=database_block.id, state="trash"
    )
    db.commit()
    result = repo.list_blocks_by_ids(db, [trashed.id], state=None)
    assert [b.id for b in result] == [trashed.id]


def test_list_blocks_by_ids_exclude_types_filters_out_entry_template(db, database_block):
    entry = repo.create_block(db, type="page", position=1.0, parent_id=database_block.id)
    template = repo.create_block(
        db, type="entry_template", position=2.0, parent_id=database_block.id
    )
    db.commit()
    result = repo.list_blocks_by_ids(
        db,
        [entry.id, template.id],
        exclude_types=frozenset({"entry_template"}),
    )
    assert [b.id for b in result] == [entry.id]


# ─── create_block ─────────────────────────────────────────────────────────────


def test_create_block_assigns_uuid(db, workspace):
    block = repo.create_block(db, type="page", position=1.0, parent_id=workspace.id)
    assert isinstance(block.id, uuid.UUID)


def test_create_block_state_defaults_to_active(db, workspace):
    block = repo.create_block(db, type="page", position=1.0, parent_id=workspace.id)
    assert block.state == "active"


def test_create_block_with_content(db, workspace):
    content = {"text": [{"plain_text": "Hello"}]}
    block = repo.create_block(
        db, type="paragraph", position=1.0, parent_id=workspace.id, content=content
    )
    assert block.content == content


def test_create_block_with_reference_id(db, workspace, database_block):
    view = repo.create_block(
        db,
        type="database_view",
        position=3.0,
        parent_id=workspace.id,
        reference_id=database_block.id,
    )
    assert view.reference_id == database_block.id


def test_create_block_without_parent(db):
    block = repo.create_block(db, type="workspace", position=0.0)
    assert block.parent_id is None


# ─── update_block ─────────────────────────────────────────────────────────────


def test_update_block_content(db, page):
    new_content = {"text": [{"plain_text": "Updated"}]}
    updated = repo.update_block(db, page, content=new_content)
    assert updated.content == new_content


def test_update_block_position(db, page):
    updated = repo.update_block(db, page, position=99.0)
    assert updated.position == 99.0


def test_update_block_state(db, page):
    updated = repo.update_block(db, page, state="trash")
    assert updated.state == "trash"


def test_update_block_parent_id(db, workspace, page):
    new_parent = repo.create_block(
        db, type="page", position=10.0, parent_id=workspace.id
    )
    db.commit()
    updated = repo.update_block(db, page, parent_id=new_parent.id)
    assert updated.parent_id == new_parent.id


def test_update_block_skips_none_fields(db, page):
    original_content = page.content
    original_position = page.position
    repo.update_block(db, page, state="trash")
    assert page.content == original_content
    assert page.position == original_position


# ─── create_schema ────────────────────────────────────────────────────────────


def test_create_schema_assigns_uuid(db, schema):
    assert isinstance(schema.id, uuid.UUID)


def test_create_schema_persists_config(db, schema):
    assert schema.config == {"options": ["Todo", "Done"]}


def test_create_schema_duplicate_name_raises(db, database_block):
    repo.create_schema(
        db, database_id=database_block.id, name="Tags", type="select", position=1.0
    )
    db.commit()
    with pytest.raises(IntegrityError):
        repo.create_schema(
            db, database_id=database_block.id, name="Tags", type="select", position=2.0
        )


def test_create_schema_same_name_different_database(db, workspace):
    db_a = repo.create_block(db, type="database", position=1.0, parent_id=workspace.id)
    db_b = repo.create_block(db, type="database", position=2.0, parent_id=workspace.id)
    db.commit()
    repo.create_schema(
        db, database_id=db_a.id, name="Status", type="select", position=1.0
    )
    repo.create_schema(
        db, database_id=db_b.id, name="Status", type="select", position=1.0
    )
    db.commit()


# ─── get_schema ───────────────────────────────────────────────────────────────


def test_get_schema_returns_existing(db, schema):
    result = repo.get_schema(db, schema.id)
    assert result is not None
    assert result.id == schema.id


def test_get_schema_returns_none_for_unknown(db):
    result = repo.get_schema(db, uuid.uuid4())
    assert result is None


# ─── update_schema ────────────────────────────────────────────────────────────


def test_update_schema_name(db, schema):
    updated = repo.update_schema(db, schema, name="Priority")
    assert updated.name == "Priority"


def test_update_schema_type(db, schema):
    updated = repo.update_schema(db, schema, type="text")
    assert updated.type == "text"


def test_update_schema_config(db, schema):
    new_config = {"options": ["Low", "High"]}
    updated = repo.update_schema(db, schema, config=new_config)
    assert updated.config == new_config


def test_update_schema_position(db, schema):
    updated = repo.update_schema(db, schema, position=99.0)
    assert updated.position == 99.0


def test_update_schema_skips_none_fields(db, schema):
    original_name = schema.name
    original_type = schema.type
    repo.update_schema(db, schema, position=50.0)
    assert schema.name == original_name
    assert schema.type == original_type


def test_update_schema_duplicate_name_raises(db, database_block, schema):
    repo.create_schema(
        db, database_id=database_block.id, name="Notes", type="text", position=2.0
    )
    db.commit()
    with pytest.raises(IntegrityError):
        repo.update_schema(db, schema, name="Notes")
        db.commit()


# ─── delete_schema ────────────────────────────────────────────────────────────


def test_delete_schema_removes_schema(db, database_block, schema):
    schema_id = schema.id
    repo.delete_schema(db, schema)
    db.commit()
    assert repo.get_schema(db, schema_id) is None


def test_delete_schema_cascades_values(db, page, database_block, schema):
    repo.upsert_value(db, page_id=page.id, schema_id=schema.id, value={"text": "x"})
    db.commit()
    values_before = repo.list_values(db, page.id)
    assert len(values_before) == 1

    repo.delete_schema(db, schema)
    db.commit()

    values_after = repo.list_values(db, page.id)
    assert len(values_after) == 0


def test_delete_schema_does_not_affect_other_schemas(db, database_block, schema):
    other = repo.create_schema(
        db, database_id=database_block.id, name="Notes", type="text", position=2.0
    )
    db.commit()
    repo.delete_schema(db, schema)
    db.commit()
    assert repo.get_schema(db, other.id) is not None


# ─── get_schema_by_name ───────────────────────────────────────────────────────


def test_get_schema_by_name_returns_schema(db, database_block, schema):
    result = repo.get_schema_by_name(db, database_block.id, "Status")
    assert result is not None
    assert result.id == schema.id


def test_get_schema_by_name_returns_none_for_unknown(db, database_block):
    result = repo.get_schema_by_name(db, database_block.id, "Nonexistent")
    assert result is None


def test_get_schema_by_name_is_case_sensitive(db, database_block, schema):
    result = repo.get_schema_by_name(db, database_block.id, "status")
    assert result is None


# ─── list_schemas ─────────────────────────────────────────────────────────────


def test_list_schemas_returns_all_schemas(db, database_block):
    repo.create_schema(
        db, database_id=database_block.id, name="A", type="text", position=2.0
    )
    repo.create_schema(
        db, database_id=database_block.id, name="B", type="text", position=1.0
    )
    db.commit()
    schemas = repo.list_schemas(db, database_block.id)
    assert len(schemas) == 2


def test_list_schemas_ordered_by_position(db, database_block):
    repo.create_schema(
        db, database_id=database_block.id, name="Z", type="text", position=3.0
    )
    repo.create_schema(
        db, database_id=database_block.id, name="A", type="text", position=1.0
    )
    db.commit()
    schemas = repo.list_schemas(db, database_block.id)
    positions = [s.position for s in schemas]
    assert positions == sorted(positions)


def test_list_schemas_empty_for_new_database(db, database_block):
    schemas = repo.list_schemas(db, database_block.id)
    assert schemas == []


# ─── list_relation_schemas_by_key_property ────────────────────────────────────
#
# Keying stores a read-side pointer at config.keying.key_property_id, which no
# foreign key can protect. The scan is what deleting or retyping the referenced
# property relies on to find its referrers, so it has to be exact about which
# configs count as a reference and which do not.


def _keyed_relation(db, database_id, name, target_database_id, key_property_id,
                    position=1.0, enabled=True):
    """Create a relation schema keyed on *key_property_id* of the target DB."""
    return repo.create_schema(
        db,
        database_id=database_id,
        name=name,
        type="relation",
        position=position,
        config={
            "target_database_id": str(target_database_id),
            "direction": "unilateral",
            "keying": {
                "enabled": enabled,
                "key_property_id": str(key_property_id),
                "key_order": "asc",
                "key_empty_first": False,
            },
        },
    )


def test_list_relation_schemas_by_key_property_empty_when_unreferenced(db, schema):
    assert repo.list_relation_schemas_by_key_property(db, schema.id) == []


def test_list_relation_schemas_by_key_property_finds_referrer(db, workspace, database_block, schema):
    other_db = repo.create_block(db, type="database", position=3.0, parent_id=workspace.id)
    relation = _keyed_relation(db, other_db.id, "Linked", database_block.id, schema.id)
    db.commit()

    result = repo.list_relation_schemas_by_key_property(db, schema.id)
    assert [r.id for r in result] == [relation.id]


def test_list_relation_schemas_by_key_property_scans_across_databases(db, workspace, database_block, schema):
    """A referrer lives in another database than the property it keys on."""
    db_b = repo.create_block(db, type="database", position=3.0, parent_id=workspace.id)
    db_c = repo.create_block(db, type="database", position=4.0, parent_id=workspace.id)
    _keyed_relation(db, db_b.id, "From B", database_block.id, schema.id)
    _keyed_relation(db, db_c.id, "From C", database_block.id, schema.id)
    db.commit()

    result = repo.list_relation_schemas_by_key_property(db, schema.id)
    assert {r.name for r in result} == {"From B", "From C"}


def test_list_relation_schemas_by_key_property_ignores_other_key_property(db, workspace, database_block, schema):
    other_prop = repo.create_schema(
        db, database_id=database_block.id, name="Rank", type="number", position=2.0
    )
    db.commit()
    other_db = repo.create_block(db, type="database", position=3.0, parent_id=workspace.id)
    _keyed_relation(db, other_db.id, "Linked", database_block.id, other_prop.id)
    db.commit()

    assert repo.list_relation_schemas_by_key_property(db, schema.id) == []


def test_list_relation_schemas_by_key_property_ignores_disabled_keying(db, workspace, database_block, schema):
    """A relation reset to vanilla keeps its pointer but is no longer a referrer."""
    other_db = repo.create_block(db, type="database", position=3.0, parent_id=workspace.id)
    _keyed_relation(db, other_db.id, "Linked", database_block.id, schema.id, enabled=False)
    db.commit()

    assert repo.list_relation_schemas_by_key_property(db, schema.id) == []


def test_list_relation_schemas_by_key_property_ignores_relation_without_keying(db, workspace, database_block, schema):
    other_db = repo.create_block(db, type="database", position=3.0, parent_id=workspace.id)
    repo.create_schema(
        db,
        database_id=other_db.id,
        name="Plain",
        type="relation",
        position=1.0,
        config={"target_database_id": str(database_block.id), "direction": "unilateral"},
    )
    db.commit()

    assert repo.list_relation_schemas_by_key_property(db, schema.id) == []


def test_list_relation_schemas_by_key_property_tolerates_null_config(db, workspace, database_block, schema):
    other_db = repo.create_block(db, type="database", position=3.0, parent_id=workspace.id)
    repo.create_schema(
        db, database_id=other_db.id, name="NoConfig", type="relation", position=1.0
    )
    db.commit()

    assert repo.list_relation_schemas_by_key_property(db, schema.id) == []


def test_list_relation_schemas_by_key_property_ignores_non_relation_types(db, workspace, database_block, schema):
    """Only relation schemas can be keyed; a keying block elsewhere is inert."""
    other_db = repo.create_block(db, type="database", position=3.0, parent_id=workspace.id)
    repo.create_schema(
        db,
        database_id=other_db.id,
        name="Impostor",
        type="text",
        position=1.0,
        config={"keying": {"enabled": True, "key_property_id": str(schema.id)}},
    )
    db.commit()

    assert repo.list_relation_schemas_by_key_property(db, schema.id) == []


def test_list_relation_schemas_by_key_property_ordered_by_position(db, workspace, database_block, schema):
    other_db = repo.create_block(db, type="database", position=3.0, parent_id=workspace.id)
    _keyed_relation(db, other_db.id, "Second", database_block.id, schema.id, position=2.0)
    _keyed_relation(db, other_db.id, "First", database_block.id, schema.id, position=1.0)
    db.commit()

    result = repo.list_relation_schemas_by_key_property(db, schema.id)
    assert [r.name for r in result] == ["First", "Second"]


# ─── list_values_for_pages ────────────────────────────────────────────────────


def test_list_values_for_pages_returns_grouped_values(db, workspace, database_block, schema):
    entry_a = repo.create_block(db, type="page", position=1.0, parent_id=database_block.id)
    entry_b = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    db.commit()
    repo.upsert_value(db, page_id=entry_a.id, schema_id=schema.id, value={"text": "A"})
    repo.upsert_value(db, page_id=entry_b.id, schema_id=schema.id, value={"text": "B"})
    db.commit()

    result = repo.list_values_for_pages(db, [entry_a.id, entry_b.id])
    assert len(result[entry_a.id]) == 1
    assert result[entry_a.id][0].value == {"text": "A"}
    assert len(result[entry_b.id]) == 1
    assert result[entry_b.id][0].value == {"text": "B"}


def test_list_values_for_pages_empty_for_pages_without_values(db, page):
    result = repo.list_values_for_pages(db, [page.id])
    assert result[page.id] == []


def test_list_values_for_pages_returns_empty_dict_for_no_ids(db):
    result = repo.list_values_for_pages(db, [])
    assert result == {}


def test_list_values_for_pages_all_ids_present_as_keys(db, workspace, database_block, schema):
    entry_a = repo.create_block(db, type="page", position=1.0, parent_id=database_block.id)
    entry_b = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    db.commit()
    repo.upsert_value(db, page_id=entry_a.id, schema_id=schema.id, value={"text": "X"})
    db.commit()

    result = repo.list_values_for_pages(db, [entry_a.id, entry_b.id])
    assert entry_a.id in result
    assert entry_b.id in result
    assert result[entry_b.id] == []


# ─── upsert_value ─────────────────────────────────────────────────────────────


def test_upsert_value_creates_new_record(db, page, schema):
    pv = repo.upsert_value(
        db, page_id=page.id, schema_id=schema.id, value={"text": "Hello"}
    )
    assert pv.id is not None
    assert pv.value == {"text": "Hello"}


def test_upsert_value_updates_existing_record(db, page, schema):
    repo.upsert_value(db, page_id=page.id, schema_id=schema.id, value={"text": "A"})
    db.commit()
    pv = repo.upsert_value(
        db, page_id=page.id, schema_id=schema.id, value={"text": "B"}
    )
    assert pv.value == {"text": "B"}


def test_upsert_value_accepts_none_value(db, page, schema):
    pv = repo.upsert_value(db, page_id=page.id, schema_id=schema.id, value=None)
    assert pv.value is None


def test_upsert_idempotent_on_same_value(db, page, schema):
    repo.upsert_value(db, page_id=page.id, schema_id=schema.id, value={"n": 1})
    db.commit()
    pv = repo.upsert_value(db, page_id=page.id, schema_id=schema.id, value={"n": 1})
    assert pv.value == {"n": 1}


# ─── get_value / list_values ──────────────────────────────────────────────────


def test_get_value_returns_existing(db, page, schema):
    repo.upsert_value(db, page_id=page.id, schema_id=schema.id, value={"x": 1})
    db.commit()
    pv = repo.get_value(db, page.id, schema.id)
    assert pv is not None
    assert pv.value == {"x": 1}


def test_get_value_returns_none_for_missing(db, page, schema):
    pv = repo.get_value(db, page.id, schema.id)
    assert pv is None


def test_list_values_returns_all_values_for_page(db, page, database_block):
    s1 = repo.create_schema(
        db, database_id=database_block.id, name="Title", type="text", position=1.0
    )
    s2 = repo.create_schema(
        db, database_id=database_block.id, name="Due", type="date", position=2.0
    )
    db.commit()
    repo.upsert_value(db, page_id=page.id, schema_id=s1.id, value={"text": "X"})
    repo.upsert_value(db, page_id=page.id, schema_id=s2.id, value={"date": "2026-01-01"})
    db.commit()
    values = repo.list_values(db, page.id)
    assert len(values) == 2


def test_list_values_empty_for_page_without_values(db, page):
    values = repo.list_values(db, page.id)
    assert values == []


# ─── query_entries ────────────────────────────────────────────────────────────


def _g(*filters) -> repo.FilterGroupDescriptor:
    """Wrap filters in a single AND group for test convenience."""
    return repo.FilterGroupDescriptor(conjunction='and', filters=list(filters))


def _make_entry(db, database_block, title=None, position=None):
    """Create an active page entry inside database_block."""
    pos = position if position is not None else 1.0
    entry = repo.create_block(
        db, type="page", position=pos, parent_id=database_block.id,
        content={"title": title} if title else None,
    )
    db.commit()
    return entry


def test_query_entries_returns_all_when_no_filters(db, database_block):
    _make_entry(db, database_block, "Alpha", position=1.0)
    _make_entry(db, database_block, "Beta",  position=2.0)
    entries, total = repo.query_entries(db, database_block.id, [], [])
    assert total == 2
    assert len(entries) == 2


def test_query_entries_excludes_entry_template_blocks(db, database_block):
    """entry_template blocks must never appear in query_entries results."""
    _make_entry(db, database_block, "Real entry", position=1.0)
    repo.create_block(
        db, type="entry_template", position=2.0, parent_id=database_block.id,
        content={"title": "My Template"},
    )
    db.commit()
    entries, total = repo.query_entries(db, database_block.id, [], [])
    assert total == 1
    assert all(e.type != "entry_template" for e in entries)


def test_query_entries_empty_database(db, database_block):
    entries, total = repo.query_entries(db, database_block.id, [], [])
    assert total == 0
    assert entries == []


def test_query_entries_name_contains_filter(db, database_block):
    _make_entry(db, database_block, "Napoleon", position=1.0)
    _make_entry(db, database_block, "Wellington", position=2.0)
    f = repo.FilterDescriptor(
        schema_id="__name__", schema_type=None, schema_config=None,
        operator="contains", value="leon",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].content["title"] == "Napoleon"


def test_query_entries_name_not_contains_filter(db, database_block):
    _make_entry(db, database_block, "Napoleon", position=1.0)
    _make_entry(db, database_block, "Wellington", position=2.0)
    f = repo.FilterDescriptor(
        schema_id="__name__", schema_type=None, schema_config=None,
        operator="not_contains", value="leon",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].content["title"] == "Wellington"


def test_query_entries_name_eq_filter(db, database_block):
    _make_entry(db, database_block, "Napoleon", position=1.0)
    _make_entry(db, database_block, "Wellington", position=2.0)
    f = repo.FilterDescriptor(
        schema_id="__name__", schema_type=None, schema_config=None,
        operator="eq", value="napoleon",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1


def test_query_entries_name_is_empty_filter(db, database_block):
    _make_entry(db, database_block, title=None, position=1.0)
    _make_entry(db, database_block, "Napoleon", position=2.0)
    f = repo.FilterDescriptor(
        schema_id="__name__", schema_type=None, schema_config=None,
        operator="is_empty", value="",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].content is None or not entries[0].content.get("title")


def test_query_entries_text_schema_contains(db, workspace, database_block):
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Notes", type="text", position=1.0
    )
    db.commit()
    e1 = _make_entry(db, database_block, position=1.0)
    e2 = _make_entry(db, database_block, position=2.0)
    repo.upsert_value(db, page_id=e1.id, schema_id=schema.id, value={"text": "cavalry charge"})
    repo.upsert_value(db, page_id=e2.id, schema_id=schema.id, value={"text": "infantry"})
    db.commit()

    f = repo.FilterDescriptor(
        schema_id=str(schema.id), schema_type="text", schema_config=None,
        operator="contains", value="cavalry",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].id == e1.id


def test_query_entries_number_gt_filter(db, workspace, database_block):
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Rank", type="number", position=1.0
    )
    db.commit()
    e1 = _make_entry(db, database_block, position=1.0)
    e2 = _make_entry(db, database_block, position=2.0)
    e3 = _make_entry(db, database_block, position=3.0)
    repo.upsert_value(db, page_id=e1.id, schema_id=schema.id, value={"number": 1})
    repo.upsert_value(db, page_id=e2.id, schema_id=schema.id, value={"number": 5})
    repo.upsert_value(db, page_id=e3.id, schema_id=schema.id, value={"number": 10})
    db.commit()

    f = repo.FilterDescriptor(
        schema_id=str(schema.id), schema_type="number", schema_config=None,
        operator="gt", value="4",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 2
    ids = {e.id for e in entries}
    assert e2.id in ids
    assert e3.id in ids


def test_query_entries_is_empty_filter(db, workspace, database_block):
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Notes", type="text", position=1.0
    )
    db.commit()
    e1 = _make_entry(db, database_block, position=1.0)
    e2 = _make_entry(db, database_block, position=2.0)
    repo.upsert_value(db, page_id=e1.id, schema_id=schema.id, value={"text": "has value"})
    db.commit()

    f = repo.FilterDescriptor(
        schema_id=str(schema.id), schema_type="text", schema_config=None,
        operator="is_empty", value="",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].id == e2.id


def test_query_entries_multiple_filters_are_anded(db, workspace, database_block):
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Rank", type="number", position=1.0
    )
    db.commit()
    e1 = _make_entry(db, database_block, "Alpha",   position=1.0)
    e2 = _make_entry(db, database_block, "Bravo",   position=2.0)
    e3 = _make_entry(db, database_block, "Charlie", position=3.0)
    repo.upsert_value(db, page_id=e1.id, schema_id=schema.id, value={"number": 1})
    repo.upsert_value(db, page_id=e2.id, schema_id=schema.id, value={"number": 5})
    repo.upsert_value(db, page_id=e3.id, schema_id=schema.id, value={"number": 10})
    db.commit()

    name_f = repo.FilterDescriptor(
        schema_id="__name__", schema_type=None, schema_config=None,
        operator="contains", value="a",
    )
    num_f = repo.FilterDescriptor(
        schema_id=str(schema.id), schema_type="number", schema_config=None,
        operator="gte", value="5",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(name_f, num_f)], [])
    # "Bravo" contains "a" and rank >= 5; "Charlie" contains "a" but rank < 5 is False,
    # actually "Charlie" rank=10 >= 5 and "Charlie" contains "a" → both match
    # "Alpha" rank=1 < 5 → excluded
    assert total == 2
    ids = {e.id for e in entries}
    assert e1.id not in ids


def test_query_entries_sort_by_name_asc(db, database_block):
    _make_entry(db, database_block, "Zeta",  position=1.0)
    _make_entry(db, database_block, "Alpha", position=2.0)
    _make_entry(db, database_block, "Mu",    position=3.0)

    s = repo.SortDescriptor(schema_id="__name__", schema_type=None, direction="asc")
    entries, _ = repo.query_entries(db, database_block.id, [], [s])
    titles = [e.content["title"] for e in entries]
    assert titles == sorted(titles, key=str.lower)


def test_query_entries_sort_by_name_desc(db, database_block):
    _make_entry(db, database_block, "Zeta",  position=1.0)
    _make_entry(db, database_block, "Alpha", position=2.0)

    s = repo.SortDescriptor(schema_id="__name__", schema_type=None, direction="desc")
    entries, _ = repo.query_entries(db, database_block.id, [], [s])
    titles = [e.content["title"] for e in entries]
    assert titles == sorted(titles, key=str.lower, reverse=True)


def test_query_entries_sort_by_number_schema(db, workspace, database_block):
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Rank", type="number", position=1.0
    )
    db.commit()
    e1 = _make_entry(db, database_block, position=1.0)
    e2 = _make_entry(db, database_block, position=2.0)
    e3 = _make_entry(db, database_block, position=3.0)
    repo.upsert_value(db, page_id=e1.id, schema_id=schema.id, value={"number": 30})
    repo.upsert_value(db, page_id=e2.id, schema_id=schema.id, value={"number": 10})
    repo.upsert_value(db, page_id=e3.id, schema_id=schema.id, value={"number": 20})
    db.commit()

    s = repo.SortDescriptor(schema_id=str(schema.id), schema_type="number", direction="asc")
    entries, _ = repo.query_entries(db, database_block.id, [], [s])
    assert entries[0].id == e2.id
    assert entries[1].id == e3.id
    assert entries[2].id == e1.id


def test_query_entries_sort_by_rollup_earliest_date(db, workspace, database_block):
    """
    Sorting by a rollup column whose function is ``earliest_date`` must order
    rows chronologically by the ISO date string stored under ``result``.

    Regression guard: before the dedicated rollup sort branch the generic text
    fallback looked up a non-existent key, so every row sorted as NULL and the
    column was effectively unsorted.
    """
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Birthday",
        type="rollup", position=1.0,
        config={"function": "earliest_date"},
    )
    db.commit()
    e1 = _make_entry(db, database_block, position=1.0)
    e2 = _make_entry(db, database_block, position=2.0)
    e3 = _make_entry(db, database_block, position=3.0)
    # Rollup values are readonly via the API but writable at the repository layer.
    repo.upsert_value(db, page_id=e1.id, schema_id=schema.id,
                      value={"result": "1990-06-15", "function": "earliest_date"})
    repo.upsert_value(db, page_id=e2.id, schema_id=schema.id,
                      value={"result": "1980-01-02", "function": "earliest_date"})
    repo.upsert_value(db, page_id=e3.id, schema_id=schema.id,
                      value={"result": "1985-12-31", "function": "earliest_date"})
    db.commit()

    s = repo.SortDescriptor(
        schema_id=str(schema.id), schema_type="rollup",
        schema_config={"function": "earliest_date"}, direction="asc",
    )
    entries, _ = repo.query_entries(db, database_block.id, [], [s])
    assert [e.id for e in entries] == [e2.id, e3.id, e1.id]

    s_desc = repo.SortDescriptor(
        schema_id=str(schema.id), schema_type="rollup",
        schema_config={"function": "earliest_date"}, direction="desc",
    )
    entries_desc, _ = repo.query_entries(db, database_block.id, [], [s_desc])
    assert [e.id for e in entries_desc] == [e1.id, e3.id, e2.id]


def test_query_entries_sort_by_numeric_rollup_orders_numerically(db, workspace, database_block):
    """
    A numeric rollup (e.g. ``sum``) must sort by numeric value, not by the
    lexicographic order of the stringified number (which would place 100
    before 20).
    """
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Total",
        type="rollup", position=1.0,
        config={"function": "sum"},
    )
    db.commit()
    e1 = _make_entry(db, database_block, position=1.0)
    e2 = _make_entry(db, database_block, position=2.0)
    e3 = _make_entry(db, database_block, position=3.0)
    repo.upsert_value(db, page_id=e1.id, schema_id=schema.id,
                      value={"result": 100, "function": "sum"})
    repo.upsert_value(db, page_id=e2.id, schema_id=schema.id,
                      value={"result": 20, "function": "sum"})
    repo.upsert_value(db, page_id=e3.id, schema_id=schema.id,
                      value={"result": 90, "function": "sum"})
    db.commit()

    s = repo.SortDescriptor(
        schema_id=str(schema.id), schema_type="rollup",
        schema_config={"function": "sum"}, direction="asc",
    )
    entries, _ = repo.query_entries(db, database_block.id, [], [s])
    assert [e.id for e in entries] == [e2.id, e3.id, e1.id]


def test_query_entries_pagination_limit(db, database_block):
    for i in range(5):
        _make_entry(db, database_block, f"Entry {i}", position=float(i))
    entries, total = repo.query_entries(db, database_block.id, [], [], limit=2, offset=0)
    assert total == 5
    assert len(entries) == 2


def test_query_entries_pagination_offset(db, database_block):
    for i in range(5):
        _make_entry(db, database_block, f"Entry {i}", position=float(i))
    entries, total = repo.query_entries(db, database_block.id, [], [], limit=2, offset=3)
    assert total == 5
    assert len(entries) == 2


def test_query_entries_pagination_beyond_total(db, database_block):
    _make_entry(db, database_block, "Only", position=1.0)
    entries, total = repo.query_entries(db, database_block.id, [], [], limit=10, offset=5)
    assert total == 1
    assert entries == []


def test_query_entries_excludes_trashed_entries(db, database_block):
    active = _make_entry(db, database_block, "Active", position=1.0)
    trashed = repo.create_block(
        db, type="page", position=2.0, parent_id=database_block.id,
        content={"title": "Trashed"}, state="trash",
    )
    db.commit()
    entries, total = repo.query_entries(db, database_block.id, [], [])
    assert total == 1
    assert entries[0].id == active.id


# ─── Null-safety: negative operators on name column ───────────────────────────


def test_query_name_neq_includes_entries_with_no_title(db, database_block):
    """neq must not exclude entries whose title is NULL."""
    titled   = _make_entry(db, database_block, "Napoleon", position=1.0)
    untitled = _make_entry(db, database_block, title=None, position=2.0)
    f = repo.FilterDescriptor(
        schema_id="__name__", schema_type=None, schema_config=None,
        operator="neq", value="Napoleon",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    ids = {e.id for e in entries}
    assert titled.id not in ids
    assert untitled.id in ids


def test_query_name_not_contains_includes_entries_with_no_title(db, database_block):
    """not_contains must not exclude entries whose title is NULL."""
    titled   = _make_entry(db, database_block, "Napoleon", position=1.0)
    untitled = _make_entry(db, database_block, title=None, position=2.0)
    f = repo.FilterDescriptor(
        schema_id="__name__", schema_type=None, schema_config=None,
        operator="not_contains", value="Napoleon",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    ids = {e.id for e in entries}
    assert titled.id not in ids
    assert untitled.id in ids


def test_query_name_contains_excludes_entries_with_no_title(db, database_block):
    """contains must not match NULL titles."""
    _make_entry(db, database_block, title=None, position=1.0)
    titled = _make_entry(db, database_block, "Napoleon", position=2.0)
    f = repo.FilterDescriptor(
        schema_id="__name__", schema_type=None, schema_config=None,
        operator="contains", value="n",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].id == titled.id


# ─── Negative operator correctness ────────────────────────────────────────────


def test_query_entries_number_neq_includes_entries_with_no_value(db, workspace, database_block):
    """neq must match entries that have no PropertyValue row at all."""
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Rank", type="number", position=1.0
    )
    db.commit()
    e_has_value = _make_entry(db, database_block, position=1.0)
    e_no_value  = _make_entry(db, database_block, position=2.0)
    repo.upsert_value(db, page_id=e_has_value.id, schema_id=schema.id, value={"number": 5})
    db.commit()

    f = repo.FilterDescriptor(
        schema_id=str(schema.id), schema_type="number", schema_config=None,
        operator="neq", value="5",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].id == e_no_value.id


def test_query_entries_number_neq_excludes_matching_value(db, workspace, database_block):
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Rank", type="number", position=1.0
    )
    db.commit()
    e1 = _make_entry(db, database_block, position=1.0)
    e2 = _make_entry(db, database_block, position=2.0)
    repo.upsert_value(db, page_id=e1.id, schema_id=schema.id, value={"number": 5})
    repo.upsert_value(db, page_id=e2.id, schema_id=schema.id, value={"number": 9})
    db.commit()

    f = repo.FilterDescriptor(
        schema_id=str(schema.id), schema_type="number", schema_config=None,
        operator="neq", value="5",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].id == e2.id


def test_query_entries_text_neq_includes_entries_with_no_value(db, workspace, database_block):
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Unit", type="text", position=1.0
    )
    db.commit()
    e_has_value = _make_entry(db, database_block, position=1.0)
    e_no_value  = _make_entry(db, database_block, position=2.0)
    repo.upsert_value(db, page_id=e_has_value.id, schema_id=schema.id, value={"text": "cavalry"})
    db.commit()

    f = repo.FilterDescriptor(
        schema_id=str(schema.id), schema_type="text", schema_config=None,
        operator="neq", value="cavalry",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].id == e_no_value.id


def test_query_entries_text_not_contains_includes_entries_with_no_value(db, workspace, database_block):
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Unit", type="text", position=1.0
    )
    db.commit()
    e_match   = _make_entry(db, database_block, position=1.0)
    e_no_val  = _make_entry(db, database_block, position=2.0)
    repo.upsert_value(db, page_id=e_match.id, schema_id=schema.id, value={"text": "cavalry"})
    db.commit()

    f = repo.FilterDescriptor(
        schema_id=str(schema.id), schema_type="text", schema_config=None,
        operator="not_contains", value="cavalry",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    # e_no_val has no value → doesn't contain "cavalry" → should match
    assert total == 1
    assert entries[0].id == e_no_val.id


# ─── Formula filter ───────────────────────────────────────────────────────────


def _make_formula_schema(db, database_block, name="FormulaCol", position=99.0):
    """Create a formula schema and return it."""
    schema = repo.create_schema(
        db, database_id=database_block.id,
        name=name, type="formula",
        position=position, config={"expression": "1"},
    )
    db.commit()
    return schema


def _make_formula_entry(db, database_block, schema, position, result, result_type):
    """Create an entry and upsert a formula value for the given schema."""
    entry = _make_entry(db, database_block, position=float(position))
    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema.id,
        value={"result": result, "result_type": result_type},
    )
    db.commit()
    return entry


def test_formula_filter_number_eq(db, workspace, database_block):
    s  = _make_formula_schema(db, database_block)
    e1 = _make_formula_entry(db, database_block, s, 1, 42.0, "number")
    e2 = _make_formula_entry(db, database_block, s, 2, 99.0, "number")  # noqa: F841
    f = repo.FilterDescriptor(
        schema_id=str(s.id), schema_type="formula", schema_config=None,
        operator="eq", value="42", formula_result_type="number",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].id == e1.id


def test_formula_filter_number_gt(db, workspace, database_block):
    s  = _make_formula_schema(db, database_block)
    e1 = _make_formula_entry(db, database_block, s, 1, 10.0, "number")  # noqa: F841
    e2 = _make_formula_entry(db, database_block, s, 2, 50.0, "number")
    f = repo.FilterDescriptor(
        schema_id=str(s.id), schema_type="formula", schema_config=None,
        operator="gt", value="20", formula_result_type="number",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].id == e2.id


def test_formula_filter_boolean_eq_true(db, workspace, database_block):
    s       = _make_formula_schema(db, database_block)
    e_true  = _make_formula_entry(db, database_block, s, 1, True,  "boolean")
    e_false = _make_formula_entry(db, database_block, s, 2, False, "boolean")  # noqa: F841
    f = repo.FilterDescriptor(
        schema_id=str(s.id), schema_type="formula", schema_config=None,
        operator="eq", value="true", formula_result_type="boolean",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].id == e_true.id


def test_formula_filter_text_contains(db, workspace, database_block):
    s  = _make_formula_schema(db, database_block)
    e1 = _make_formula_entry(db, database_block, s, 1, "hello world", "text")
    e2 = _make_formula_entry(db, database_block, s, 2, "foo bar",     "text")  # noqa: F841
    f = repo.FilterDescriptor(
        schema_id=str(s.id), schema_type="formula", schema_config=None,
        operator="contains", value="hello", formula_result_type="text",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].id == e1.id


def test_formula_filter_is_empty(db, workspace, database_block):
    s       = _make_formula_schema(db, database_block)
    e_val   = _make_formula_entry(db, database_block, s, 1, "x",  "text")  # noqa: F841
    e_empty = _make_formula_entry(db, database_block, s, 2, None, "text")
    f = repo.FilterDescriptor(
        schema_id=str(s.id), schema_type="formula", schema_config=None,
        operator="is_empty", value="",
    )
    entries, total = repo.query_entries(db, database_block.id, [_g(f)], [])
    assert total == 1
    assert entries[0].id == e_empty.id


# ─── resolve_filter_descriptor (shared filter resolver) ───────────────────────


def test_resolve_filter_descriptor_name_column():
    """The '__name__' title column resolves with no schema type or config."""
    d = repo.resolve_filter_descriptor(
        {}, schema_id="__name__", operator="contains", value="leon",
    )
    assert d is not None
    assert d.schema_id == "__name__"
    assert d.schema_type is None
    assert d.schema_config is None
    assert d.operator == "contains"
    assert d.value == "leon"


def test_resolve_filter_descriptor_known_schema_attaches_type_and_config(db, database_block):
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Status", type="select",
        position=1.0, config={"options": ["Todo", "Done"]},
    )
    db.commit()
    schema_map = {str(schema.id): schema}
    d = repo.resolve_filter_descriptor(
        schema_map, schema_id=str(schema.id), operator="eq", value="Done",
    )
    assert d is not None
    assert d.schema_type == "select"
    assert d.schema_config == {"options": ["Todo", "Done"]}
    assert d.value == "Done"


def test_resolve_filter_descriptor_unknown_schema_returns_none():
    """A schema_id absent from the map yields None so the caller can skip it."""
    d = repo.resolve_filter_descriptor(
        {}, schema_id=str(uuid.uuid4()), operator="contains", value="x",
    )
    assert d is None


def test_resolve_filter_descriptor_relation_passthrough(db, database_block):
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Linked", type="relation",
        position=1.0, config={"target_database_id": str(uuid.uuid4())},
    )
    db.commit()
    rel_uuid = str(uuid.uuid4())
    d = repo.resolve_filter_descriptor(
        {str(schema.id): schema},
        schema_id=str(schema.id), operator="contains", value=rel_uuid,
    )
    assert d is not None
    assert d.schema_type == "relation"
    assert d.value == rel_uuid


def test_resolve_filter_descriptor_formula_result_type_kept_for_formula(db, database_block):
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Calc", type="formula",
        position=1.0, config={"expression": "1+1"},
    )
    db.commit()
    d = repo.resolve_filter_descriptor(
        {str(schema.id): schema},
        schema_id=str(schema.id), operator="gt", value="1",
        formula_result_type="number",
    )
    assert d is not None
    assert d.formula_result_type == "number"


def test_resolve_filter_descriptor_formula_result_type_dropped_for_non_formula(db, database_block):
    """formula_result_type is only meaningful for formula schemas; forced None otherwise."""
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Plain", type="text", position=1.0,
    )
    db.commit()
    d = repo.resolve_filter_descriptor(
        {str(schema.id): schema},
        schema_id=str(schema.id), operator="contains", value="x",
        formula_result_type="number",
    )
    assert d is not None
    assert d.formula_result_type is None


def test_resolve_filter_descriptor_date_fields_passthrough(db, database_block):
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Due", type="date", position=1.0,
    )
    db.commit()
    d = repo.resolve_filter_descriptor(
        {str(schema.id): schema},
        schema_id=str(schema.id), operator="between",
        value="2026-01-01", value2="2026-12-31",
        date_mode="exact", date_offset=0,
    )
    assert d is not None
    assert d.value == "2026-01-01"
    assert d.value2 == "2026-12-31"
    assert d.date_mode == "exact"
    assert d.date_offset == 0
