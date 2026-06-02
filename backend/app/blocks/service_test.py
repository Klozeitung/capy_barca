"""
Tests for the block service.

All tests run against the isolated in-memory SQLite database provided by
the autouse ``isolated_db`` fixture in conftest.py. The service is called
directly; no HTTP layer is involved.
"""
import uuid

import pytest

import app.session.session as s
from app.blocks import repository as repo
from app.blocks import service
from app.blocks.models import WORKSPACE_ROOT_ID, Block, PropertySchema, PropertyValue
from app.blocks.service import BlockConflict, BlockNotFound


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
def database_a(db, workspace):
    block = repo.create_block(db, type="database", position=2.0, parent_id=workspace.id)
    db.commit()
    return block


@pytest.fixture
def database_b(db, workspace):
    block = repo.create_block(db, type="database", position=3.0, parent_id=workspace.id)
    db.commit()
    return block


@pytest.fixture
def entry(db, database_a):
    block = repo.create_block(db, type="page", position=1.0, parent_id=database_a.id)
    db.commit()
    return block


# ─── create_block ─────────────────────────────────────────────────────────────


def test_create_block_appends_after_last_child(db, workspace, page):
    new = service.create_block(db, type="page", parent_id=workspace.id)
    assert new.position > page.position


def test_create_block_uses_explicit_position(db, workspace):
    block = service.create_block(db, type="page", parent_id=workspace.id, position=99.0)
    assert block.position == 99.0


def test_create_block_raises_for_unknown_parent(db):
    with pytest.raises(BlockNotFound):
        service.create_block(db, type="page", parent_id=uuid.uuid4())


def test_create_block_first_child_gets_position_1(db, workspace):
    block = service.create_block(db, type="page", parent_id=workspace.id)
    assert block.position == 1.0


def test_create_block_raises_for_unknown_type(db, workspace):
    with pytest.raises(ValueError, match="Unknown block type"):
        service.create_block(db, type="not_a_real_type", parent_id=workspace.id)


# ─── position helpers ─────────────────────────────────────────────────────────


def test_position_after_last_with_no_children(db, workspace):
    assert service.position_after_last(db, workspace.id) == 1.0


def test_position_after_last_with_children(db, workspace, page):
    pos = service.position_after_last(db, workspace.id)
    assert pos > page.position


def test_position_between(db):
    assert service.position_between(1.0, 3.0) == 2.0


def test_position_between_fractional(db):
    assert service.position_between(1.0, 2.0) == 1.5


# ─── rebalance_positions ──────────────────────────────────────────────────────


def test_rebalance_normalises_positions_to_integers(db, workspace):
    a = repo.create_block(db, type="page", position=1.0, parent_id=workspace.id)
    b = repo.create_block(db, type="page", position=1.5, parent_id=workspace.id)
    c = repo.create_block(db, type="page", position=1.75, parent_id=workspace.id)
    db.commit()

    service.rebalance_positions(db, workspace.id)
    db.commit()

    db.refresh(a)
    db.refresh(b)
    db.refresh(c)

    assert a.position == 1.0
    assert b.position == 2.0
    assert c.position == 3.0


def test_rebalance_preserves_relative_order(db, workspace):
    a = repo.create_block(db, type="page", position=0.1, parent_id=workspace.id)
    b = repo.create_block(db, type="page", position=0.2, parent_id=workspace.id)
    c = repo.create_block(db, type="page", position=0.3, parent_id=workspace.id)
    db.commit()

    service.rebalance_positions(db, workspace.id)
    db.commit()

    db.refresh(a)
    db.refresh(b)
    db.refresh(c)

    assert a.position < b.position < c.position


def test_rebalance_returns_changed_ids(db, workspace):
    a = repo.create_block(db, type="page", position=1.0, parent_id=workspace.id)
    b = repo.create_block(db, type="page", position=1.5, parent_id=workspace.id)
    db.commit()

    changed = service.rebalance_positions(db, workspace.id)

    # a is already at 1.0 so it should not be in changed; b at 1.5 → 2.0 should
    assert b.id in changed
    assert a.id not in changed


def test_rebalance_returns_empty_when_already_normalised(db, workspace):
    repo.create_block(db, type="page", position=1.0, parent_id=workspace.id)
    repo.create_block(db, type="page", position=2.0, parent_id=workspace.id)
    db.commit()

    changed = service.rebalance_positions(db, workspace.id)
    assert changed == []


def test_rebalance_ignores_trashed_children(db, workspace):
    a = repo.create_block(db, type="page", position=1.0, parent_id=workspace.id)
    b = repo.create_block(db, type="page", position=1.5, parent_id=workspace.id)
    repo.update_block(db, b, state="trash")
    db.commit()

    service.rebalance_positions(db, workspace.id)
    db.commit()

    db.refresh(b)
    # trashed block's position is not touched
    assert b.position == 1.5


def test_move_triggers_rebalance_when_gap_is_tiny(db, workspace):
    """
    Verify that moving a block into a critically tight gap triggers an
    automatic rebalance of the destination parent's children.
    """
    a = repo.create_block(db, type="page", position=1.0, parent_id=workspace.id)
    b = repo.create_block(db, type="page", position=1.0 + 1e-10, parent_id=workspace.id)
    c = repo.create_block(db, type="page", position=3.0, parent_id=workspace.id)
    db.commit()

    # Moving c between a and b creates a sub-threshold gap – rebalance should fire
    service.move(
        db,
        c.id,
        new_parent_id=workspace.id,
        new_position=(1.0 + (1.0 + 1e-10)) / 2.0,
    )
    db.commit()

    db.refresh(a)
    db.refresh(b)
    db.refresh(c)

    positions = sorted([a.position, b.position, c.position])
    gaps = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
    assert all(gap >= 1e-9 for gap in gaps), f"Gap too small after rebalance: {gaps}"


# ─── soft_delete ──────────────────────────────────────────────────────────────


def test_soft_delete_marks_block_as_trash(db, page):
    service.soft_delete(db, page.id)
    db.refresh(page)
    assert page.state == "trash"


def test_soft_delete_cascades_to_children(db, page):
    child = repo.create_block(db, type="paragraph", position=1.0, parent_id=page.id)
    db.commit()
    service.soft_delete(db, page.id)
    db.refresh(child)
    assert child.state == "trash"


def test_soft_delete_cascades_to_grandchildren(db, page):
    child = repo.create_block(db, type="paragraph", position=1.0, parent_id=page.id)
    db.commit()
    grandchild = repo.create_block(
        db, type="paragraph", position=1.0, parent_id=child.id
    )
    db.commit()
    service.soft_delete(db, page.id)
    db.refresh(grandchild)
    assert grandchild.state == "trash"


def test_soft_delete_returns_affected_ids(db, page):
    child = repo.create_block(db, type="paragraph", position=1.0, parent_id=page.id)
    db.commit()
    affected = service.soft_delete(db, page.id)
    assert page.id in affected
    assert child.id in affected


def test_soft_delete_root_is_first_in_result(db, page):
    child = repo.create_block(db, type="paragraph", position=1.0, parent_id=page.id)
    db.commit()
    affected = service.soft_delete(db, page.id)
    assert affected[0] == page.id


def test_soft_delete_raises_for_unknown_block(db):
    with pytest.raises(BlockNotFound):
        service.soft_delete(db, uuid.uuid4())


def test_soft_delete_workspace_raises_conflict(db, workspace):
    with pytest.raises(BlockConflict):
        service.soft_delete(db, workspace.id)


def test_soft_delete_does_not_affect_siblings(db, workspace):
    sibling = repo.create_block(
        db, type="page", position=2.0, parent_id=workspace.id
    )
    page_block = repo.create_block(
        db, type="page", position=1.0, parent_id=workspace.id
    )
    db.commit()
    service.soft_delete(db, page_block.id)
    db.refresh(sibling)
    assert sibling.state == "active"


# ─── purge ────────────────────────────────────────────────────────────────────


def test_purge_removes_block_from_db(db, page):
    service.soft_delete(db, page.id)
    db.commit()
    service.purge(db, page.id)
    db.commit()
    assert repo.get_block(db, page.id) is None


def test_purge_raises_if_not_trashed(db, page):
    with pytest.raises(BlockConflict):
        service.purge(db, page.id)


def test_purge_raises_for_unknown_block(db):
    with pytest.raises(BlockNotFound):
        service.purge(db, uuid.uuid4())


def test_purge_workspace_raises_conflict(db, workspace):
    with pytest.raises(BlockConflict):
        service.purge(db, workspace.id)


def test_purge_clears_bilateral_relation_mirror(db, workspace, database_a, database_b, entry):
    """
    When a database entry with a bilateral relation is hard-deleted, its ID
    must be removed from the mirror value in the target database.
    """
    schema_a = repo.create_schema(
        db,
        database_id=database_a.id,
        name="Links",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_b.id),
            "direction": "bilateral",
            "mirror_property_name": "BackLinks",
        },
    )
    mirror_schema = repo.create_schema(
        db,
        database_id=database_b.id,
        name="BackLinks",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_a.id),
            "direction": "bilateral",
            "mirror_property_name": "Links",
        },
    )
    entry_b = repo.create_block(db, type="page", position=1.0, parent_id=database_b.id)
    db.commit()

    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_a.id,
        value={"related_ids": [str(entry_b.id)]},
    )
    repo.upsert_value(
        db, page_id=entry_b.id, schema_id=mirror_schema.id,
        value={"related_ids": [str(entry.id)]},
    )
    db.commit()

    service.soft_delete(db, entry.id)
    db.commit()
    service.purge(db, entry.id)
    db.commit()

    mirror_pv = repo.get_value(db, entry_b.id, mirror_schema.id)
    related = (mirror_pv.value or {}).get("related_ids", []) if mirror_pv else []
    assert str(entry.id) not in related


def test_purge_clears_bilateral_mirror_multiple_targets(db, workspace, database_a, database_b, entry):
    """
    Entry linked to multiple targets: all mirror values must be cleaned up.
    """
    schema_a = repo.create_schema(
        db,
        database_id=database_a.id,
        name="Links",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_b.id),
            "direction": "bilateral",
            "mirror_property_name": "BackLinks",
        },
    )
    mirror_schema = repo.create_schema(
        db,
        database_id=database_b.id,
        name="BackLinks",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_a.id),
            "direction": "bilateral",
            "mirror_property_name": "Links",
        },
    )
    entry_b1 = repo.create_block(db, type="page", position=1.0, parent_id=database_b.id)
    entry_b2 = repo.create_block(db, type="page", position=2.0, parent_id=database_b.id)
    db.commit()

    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_a.id,
        value={"related_ids": [str(entry_b1.id), str(entry_b2.id)]},
    )
    repo.upsert_value(
        db, page_id=entry_b1.id, schema_id=mirror_schema.id,
        value={"related_ids": [str(entry.id)]},
    )
    repo.upsert_value(
        db, page_id=entry_b2.id, schema_id=mirror_schema.id,
        value={"related_ids": [str(entry.id)]},
    )
    db.commit()

    service.soft_delete(db, entry.id)
    db.commit()
    service.purge(db, entry.id)
    db.commit()

    for target in (entry_b1, entry_b2):
        pv = repo.get_value(db, target.id, mirror_schema.id)
        related = (pv.value or {}).get("related_ids", []) if pv else []
        assert str(entry.id) not in related


def test_purge_returns_affected_db_ids(db, workspace, database_a, database_b, entry):
    """
    purge() must return the UUIDs of databases whose mirror values changed.
    """
    schema_a = repo.create_schema(
        db,
        database_id=database_a.id,
        name="Links",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_b.id),
            "direction": "bilateral",
            "mirror_property_name": "BackLinks",
        },
    )
    mirror_schema = repo.create_schema(
        db,
        database_id=database_b.id,
        name="BackLinks",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_a.id),
            "direction": "bilateral",
            "mirror_property_name": "Links",
        },
    )
    entry_b = repo.create_block(db, type="page", position=1.0, parent_id=database_b.id)
    db.commit()

    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_a.id,
        value={"related_ids": [str(entry_b.id)]},
    )
    repo.upsert_value(
        db, page_id=entry_b.id, schema_id=mirror_schema.id,
        value={"related_ids": [str(entry.id)]},
    )
    db.commit()

    service.soft_delete(db, entry.id)
    db.commit()
    affected = service.purge(db, entry.id)
    db.commit()

    assert str(database_b.id) in affected


def test_purge_no_bilateral_relations_returns_empty_set(db, workspace, database_a, entry):
    """
    purge() returns an empty set when the entry had no bilateral relations.
    """
    service.soft_delete(db, entry.id)
    db.commit()
    affected = service.purge(db, entry.id)
    db.commit()

    assert affected == set()


def test_purge_unilateral_relation_not_cleaned_up(db, workspace, database_a, database_b, entry):
    """
    Unilateral relations have no mirror; purge must not touch them and must
    return an empty affected set.
    """
    schema_a = repo.create_schema(
        db,
        database_id=database_a.id,
        name="Refs",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_b.id),
            "direction": "unilateral",
        },
    )
    entry_b = repo.create_block(db, type="page", position=1.0, parent_id=database_b.id)
    db.commit()

    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_a.id,
        value={"related_ids": [str(entry_b.id)]},
    )
    db.commit()

    service.soft_delete(db, entry.id)
    db.commit()
    affected = service.purge(db, entry.id)
    db.commit()

    assert affected == set()


# ON DELETE CASCADE on blocks.parent_id is a PostgreSQL-level constraint defined
# in migration 0002. It cannot be reliably tested against SQLite because SQLite
# requires PRAGMA foreign_keys=ON to enforce FKs, which in turn triggers RESTRICT
# violations during test teardown and corrupts subsequent tests. Cascade behaviour
# is covered by the migration itself and verified by running against the live
# PostgreSQL container.


# ─── restore ──────────────────────────────────────────────────────────────────


def test_restore_sets_block_to_active(db, page):
    service.soft_delete(db, page.id)
    db.commit()
    service.restore(db, page.id)
    db.refresh(page)
    assert page.state == "active"


def test_restore_cascades_to_children(db, page):
    child = repo.create_block(db, type="paragraph", position=1.0, parent_id=page.id)
    db.commit()
    service.soft_delete(db, page.id)
    db.commit()
    service.restore(db, page.id)
    db.refresh(child)
    assert child.state == "active"


def test_restore_returns_affected_ids(db, page):
    child = repo.create_block(db, type="paragraph", position=1.0, parent_id=page.id)
    db.commit()
    service.soft_delete(db, page.id)
    db.commit()
    restored = service.restore(db, page.id)
    assert page.id in restored
    assert child.id in restored


def test_restore_raises_if_not_trashed(db, page):
    with pytest.raises(BlockConflict):
        service.restore(db, page.id)


def test_restore_raises_for_unknown_block(db):
    with pytest.raises(BlockNotFound):
        service.restore(db, uuid.uuid4())


# ─── move ─────────────────────────────────────────────────────────────────────


def test_move_changes_parent(db, workspace, page):
    new_parent = repo.create_block(
        db, type="page", position=2.0, parent_id=workspace.id
    )
    db.commit()
    service.move(db, page.id, new_parent_id=new_parent.id, new_position=1.0)
    db.refresh(page)
    assert page.parent_id == new_parent.id


def test_move_changes_position(db, workspace, page):
    service.move(db, page.id, new_parent_id=workspace.id, new_position=99.0)
    db.refresh(page)
    assert page.position == 99.0


def test_move_raises_for_unknown_block(db, workspace):
    with pytest.raises(BlockNotFound):
        service.move(db, uuid.uuid4(), new_parent_id=workspace.id, new_position=1.0)


def test_move_raises_for_unknown_new_parent(db, page):
    with pytest.raises(BlockNotFound):
        service.move(db, page.id, new_parent_id=uuid.uuid4(), new_position=1.0)


def test_move_workspace_raises_conflict(db, workspace):
    with pytest.raises(BlockConflict):
        service.move(db, workspace.id, new_parent_id=workspace.id, new_position=1.0)


def test_move_into_own_subtree_raises_conflict(db, page):
    child = repo.create_block(db, type="page", position=1.0, parent_id=page.id)
    db.commit()
    with pytest.raises(BlockConflict):
        service.move(db, page.id, new_parent_id=child.id, new_position=1.0)


def test_move_into_self_raises_conflict(db, page):
    with pytest.raises(BlockConflict):
        service.move(db, page.id, new_parent_id=page.id, new_position=1.0)


def test_move_non_page_block_under_non_page_raises_conflict(db, workspace, page):
    """Non-page/-workspace blocks must not be moved under a non-page parent."""
    database = repo.create_block(
        db, type="database", position=2.0, parent_id=workspace.id
    )
    paragraph = repo.create_block(
        db, type="paragraph", position=1.0, parent_id=page.id
    )
    db.commit()
    with pytest.raises(BlockConflict):
        service.move(db, paragraph.id, new_parent_id=database.id, new_position=1.0)


def test_move_non_page_block_under_page_is_allowed(db, workspace, page):
    """Non-page blocks may be moved between page parents."""
    other_page = repo.create_block(
        db, type="page", position=2.0, parent_id=workspace.id
    )
    paragraph = repo.create_block(
        db, type="paragraph", position=1.0, parent_id=page.id
    )
    db.commit()
    result = service.move(
        db, paragraph.id, new_parent_id=other_page.id, new_position=1.0
    )
    assert result.parent_id == other_page.id


def test_move_non_page_block_under_toggle_is_allowed(db, workspace, page):
    """Content blocks may be moved under toggle blocks (foldable containers)."""
    toggle = repo.create_block(db, type="toggle", position=2.0, parent_id=page.id)
    paragraph = repo.create_block(db, type="paragraph", position=1.0, parent_id=page.id)
    db.commit()
    result = service.move(db, paragraph.id, new_parent_id=toggle.id, new_position=1.0)
    assert result.parent_id == toggle.id


def test_move_non_page_block_under_workspace_raises_conflict(db, workspace, page):
    """Content blocks must not be moved directly under the workspace root."""
    paragraph = repo.create_block(db, type="paragraph", position=1.0, parent_id=page.id)
    db.commit()
    with pytest.raises(BlockConflict):
        service.move(db, paragraph.id, new_parent_id=workspace.id, new_position=1.0)


def test_move_page_under_non_page_is_allowed(db, workspace, database_a, page):
    """A page-type block may be placed under a non-page (e.g. database)."""
    result = service.move(db, page.id, new_parent_id=database_a.id, new_position=1.0)
    assert result.parent_id == database_a.id


# ─── move with property migration (database → database) ───────────────────────


def test_move_between_databases_repoints_existing_schema(db, database_a, database_b, entry):
    schema_a = repo.create_schema(
        db, database_id=database_a.id, name="Status", type="select", position=1.0
    )
    schema_b = repo.create_schema(
        db, database_id=database_b.id, name="Status", type="select", position=1.0
    )
    db.commit()

    repo.upsert_value(db, page_id=entry.id, schema_id=schema_a.id, value={"option": "Done"})
    db.commit()

    service.move(db, entry.id, new_parent_id=database_b.id, new_position=1.0)
    db.commit()

    values = repo.list_values(db, entry.id)
    assert len(values) == 1
    assert values[0].property_schema_id == schema_b.id
    assert values[0].value == {"option": "Done"}


def test_move_between_databases_creates_missing_schema(db, database_a, database_b, entry):
    schema_a = repo.create_schema(
        db, database_id=database_a.id, name="Notes", type="text", position=1.0
    )
    db.commit()

    repo.upsert_value(db, page_id=entry.id, schema_id=schema_a.id, value={"text": "hi"})
    db.commit()

    service.move(db, entry.id, new_parent_id=database_b.id, new_position=1.0)
    db.commit()

    target_schema = repo.get_schema_by_name(db, database_b.id, "Notes")
    assert target_schema is not None
    assert target_schema.type == "text"

    values = repo.list_values(db, entry.id)
    assert values[0].property_schema_id == target_schema.id


def test_move_between_databases_does_not_port_relation_schema(
    db, database_a, database_b, entry
):
    schema_a = repo.create_schema(
        db, database_id=database_a.id, name="Related", type="relation", position=1.0
    )
    db.commit()

    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_a.id, value={"ids": []}
    )
    db.commit()

    service.move(db, entry.id, new_parent_id=database_b.id, new_position=1.0)
    db.commit()

    target_schema = repo.get_schema_by_name(db, database_b.id, "Related")
    assert target_schema is None


def test_move_within_same_parent_no_schema_migration(db, database_a, entry):
    schema_a = repo.create_schema(
        db, database_id=database_a.id, name="Status", type="select", position=1.0
    )
    db.commit()

    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_a.id, value={"option": "Todo"}
    )
    db.commit()

    service.move(db, entry.id, new_parent_id=database_a.id, new_position=99.0)
    db.commit()

    values = repo.list_values(db, entry.id)
    assert values[0].property_schema_id == schema_a.id


# ─── move with schemaMemory (database → non-database) ─────────────────────────


def test_move_db_to_non_db_writes_schema_memory(db, workspace, database_a, entry):
    """Leaving a database writes a schemaMemory snapshot into content."""
    schema = repo.create_schema(
        db, database_id=database_a.id, name="Status", type="select", position=1.0,
        config={"mode": "single", "options": ["Todo", "Done"]},
    )
    db.commit()
    repo.upsert_value(db, page_id=entry.id, schema_id=schema.id, value={"option": "Done"})
    db.commit()

    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()
    db.refresh(entry)

    memory = entry.content["schemaMemory"]
    assert len(memory) == 1
    assert memory[0]["name"] == "Status"
    assert memory[0]["type"] == "select"
    assert memory[0]["value"] == {"option": "Done"}
    assert memory[0]["config"] == {"mode": "single", "options": ["Todo", "Done"]}


def test_move_db_to_non_db_excludes_relation_from_schema_memory(
    db, workspace, database_a, entry
):
    """Relation-type properties must not appear in schemaMemory."""
    schema = repo.create_schema(
        db, database_id=database_a.id, name="Related", type="relation", position=1.0
    )
    db.commit()
    repo.upsert_value(db, page_id=entry.id, schema_id=schema.id, value={"ids": []})
    db.commit()

    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()
    db.refresh(entry)

    memory = entry.content["schemaMemory"]
    assert memory == []


def test_move_db_to_non_db_no_values_writes_empty_schema_memory(
    db, workspace, database_a, entry
):
    """An entry with no values still gets an empty schemaMemory list."""
    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()
    db.refresh(entry)

    assert "schemaMemory" in entry.content
    assert entry.content["schemaMemory"] == []


# ─── move with schemaMemory (non-database → database) ─────────────────────────


def test_move_non_db_to_db_restores_values_from_schema_memory(
    db, workspace, database_a, database_b, entry
):
    """Values written to schemaMemory are restored when entering a database."""
    schema_a = repo.create_schema(
        db, database_id=database_a.id, name="Priority", type="text", position=1.0
    )
    schema_b = repo.create_schema(
        db, database_id=database_b.id, name="Priority", type="text", position=1.0
    )
    db.commit()
    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_a.id, value={"text": "High"}
    )
    db.commit()

    # DB_A → page (writes schemaMemory)
    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()

    # page → DB_B (migrates from schemaMemory)
    service.move(db, entry.id, new_parent_id=database_b.id, new_position=1.0)
    db.commit()

    values = repo.list_values(db, entry.id)
    assert len(values) == 1
    assert values[0].property_schema_id == schema_b.id
    assert values[0].value == {"text": "High"}


def test_move_non_db_to_db_creates_missing_schema_from_memory(
    db, workspace, database_a, database_b, entry
):
    """A schema not present in the target database is created from schemaMemory."""
    schema_a = repo.create_schema(
        db, database_id=database_a.id, name="Notes", type="text", position=2.0
    )
    db.commit()
    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_a.id, value={"text": "hello"}
    )
    db.commit()

    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()
    service.move(db, entry.id, new_parent_id=database_b.id, new_position=1.0)
    db.commit()

    created = repo.get_schema_by_name(db, database_b.id, "Notes")
    assert created is not None
    assert created.type == "text"

    values = repo.list_values(db, entry.id)
    assert len(values) == 1
    assert values[0].value == {"text": "hello"}


def test_move_non_db_to_db_clears_schema_memory(
    db, workspace, database_a, database_b, entry
):
    """schemaMemory is removed from content after a successful migration."""
    schema_a = repo.create_schema(
        db, database_id=database_a.id, name="Status", type="select", position=1.0
    )
    db.commit()
    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_a.id, value={"option": "Todo"}
    )
    db.commit()

    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()
    service.move(db, entry.id, new_parent_id=database_b.id, new_position=1.0)
    db.commit()
    db.refresh(entry)

    assert "schemaMemory" not in (entry.content or {})


def test_move_non_db_to_db_deletes_orphaned_live_values(
    db, workspace, database_a, database_b, entry
):
    """
    When migrating from schemaMemory, stale PropertyValue rows that still
    reference schemas of the old database are deleted so no orphans remain.
    """
    schema_a = repo.create_schema(
        db, database_id=database_a.id, name="Tag", type="text", position=1.0
    )
    db.commit()
    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_a.id, value={"text": "x"}
    )
    db.commit()

    # DB_A → page: schemaMemory written, live value still points to schema_a
    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()

    # page → DB_B: should delete the old live value and create a fresh one
    service.move(db, entry.id, new_parent_id=database_b.id, new_position=1.0)
    db.commit()

    values = repo.list_values(db, entry.id)
    # Exactly one value, pointing to a schema in database_b (not schema_a)
    assert len(values) == 1
    assert values[0].property_schema_id != schema_a.id


def test_move_non_db_to_db_does_not_restore_relation_from_memory(
    db, workspace, database_a, database_b, entry
):
    """Relation entries in schemaMemory are skipped during migration."""
    schema_rel = repo.create_schema(
        db, database_id=database_a.id, name="Links", type="relation", position=1.0
    )
    schema_txt = repo.create_schema(
        db, database_id=database_a.id, name="Note", type="text", position=2.0
    )
    db.commit()
    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_rel.id, value={"ids": ["abc"]}
    )
    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_txt.id, value={"text": "keep"}
    )
    db.commit()

    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()
    service.move(db, entry.id, new_parent_id=database_b.id, new_position=1.0)
    db.commit()

    # Only the text property should have been migrated
    assert repo.get_schema_by_name(db, database_b.id, "Links") is None
    assert repo.get_schema_by_name(db, database_b.id, "Note") is not None
    values = repo.list_values(db, entry.id)
    assert len(values) == 1
    assert values[0].value == {"text": "keep"}


# ─── move with schemaMemory – full roundtrip ──────────────────────────────────


def test_move_roundtrip_db_to_page_and_back_preserves_values(
    db, workspace, database_a, entry
):
    """
    A complete DB → page → DB roundtrip to the same database must leave
    the entry's property values intact and schemaMemory cleared.
    """
    schema = repo.create_schema(
        db, database_id=database_a.id, name="Status", type="select", position=1.0,
        config={"mode": "single", "options": ["Todo", "Done"]},
    )
    db.commit()
    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema.id, value={"option": "Done"}
    )
    db.commit()

    # Leave the database
    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()

    # Return to the same database
    service.move(db, entry.id, new_parent_id=database_a.id, new_position=1.0)
    db.commit()
    db.refresh(entry)

    values = repo.list_values(db, entry.id)
    assert len(values) == 1
    assert values[0].property_schema_id == schema.id
    assert values[0].value == {"option": "Done"}
    assert "schemaMemory" not in (entry.content or {})


def test_move_roundtrip_preserves_values_across_different_databases(
    db, workspace, database_a, database_b, entry
):
    """
    DB_A → page → DB_B must restore values in DB_B, creating schemas where
    needed, and leave no schemaMemory in content.
    """
    schema_a = repo.create_schema(
        db, database_id=database_a.id, name="Score", type="number", position=1.0,
        config={"format": "plain"},
    )
    db.commit()
    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_a.id, value={"number": 42}
    )
    db.commit()

    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()
    service.move(db, entry.id, new_parent_id=database_b.id, new_position=1.0)
    db.commit()
    db.refresh(entry)

    schema_b = repo.get_schema_by_name(db, database_b.id, "Score")
    assert schema_b is not None
    assert schema_b.type == "number"

    values = repo.list_values(db, entry.id)
    assert len(values) == 1
    assert values[0].value == {"number": 42}
    assert "schemaMemory" not in (entry.content or {})


# ─── move: bilateral relation cleanup on DB → non-DB ─────────────────────────


def test_move_db_to_non_db_clears_bilateral_mirror(
    db, workspace, database_a, database_b, entry
):
    """
    When an entry leaves a database, its ID must be removed from the mirror
    value of every entry it was bilaterally linked to.
    """
    # entry_b lives in database_b and is linked back to entry via a bilateral
    # relation originating in database_a.
    schema_a = repo.create_schema(
        db,
        database_id=database_a.id,
        name="Links",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_b.id),
            "direction": "bilateral",
            "mirror_property_name": "BackLinks",
        },
    )
    mirror_schema = repo.create_schema(
        db,
        database_id=database_b.id,
        name="BackLinks",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_a.id),
            "direction": "bilateral",
            "mirror_property_name": "Links",
        },
    )
    entry_b = repo.create_block(
        db, type="page", position=1.0, parent_id=database_b.id
    )
    db.commit()

    # entry → entry_b (bilateral link)
    repo.upsert_value(
        db,
        page_id=entry.id,
        schema_id=schema_a.id,
        value={"related_ids": [str(entry_b.id)]},
    )
    # mirror: entry_b → entry
    repo.upsert_value(
        db,
        page_id=entry_b.id,
        schema_id=mirror_schema.id,
        value={"related_ids": [str(entry.id)]},
    )
    db.commit()

    # Move entry out of database_a → should clean up mirror in database_b
    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()

    mirror_pv = repo.get_value(db, entry_b.id, mirror_schema.id)
    related = (mirror_pv.value or {}).get("related_ids", []) if mirror_pv else []
    assert str(entry.id) not in related


def test_move_db_to_non_db_clears_bilateral_mirror_multiple_linked(
    db, workspace, database_a, database_b, entry
):
    """
    Entry linked to multiple targets: all mirrors must be cleaned up.
    """
    schema_a = repo.create_schema(
        db,
        database_id=database_a.id,
        name="Links",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_b.id),
            "direction": "bilateral",
            "mirror_property_name": "BackLinks",
        },
    )
    mirror_schema = repo.create_schema(
        db,
        database_id=database_b.id,
        name="BackLinks",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_a.id),
            "direction": "bilateral",
            "mirror_property_name": "Links",
        },
    )
    entry_b1 = repo.create_block(db, type="page", position=1.0, parent_id=database_b.id)
    entry_b2 = repo.create_block(db, type="page", position=2.0, parent_id=database_b.id)
    db.commit()

    repo.upsert_value(
        db,
        page_id=entry.id,
        schema_id=schema_a.id,
        value={"related_ids": [str(entry_b1.id), str(entry_b2.id)]},
    )
    repo.upsert_value(
        db, page_id=entry_b1.id, schema_id=mirror_schema.id,
        value={"related_ids": [str(entry.id)]},
    )
    repo.upsert_value(
        db, page_id=entry_b2.id, schema_id=mirror_schema.id,
        value={"related_ids": [str(entry.id)]},
    )
    db.commit()

    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()

    for eb in (entry_b1, entry_b2):
        pv = repo.get_value(db, eb.id, mirror_schema.id)
        related = (pv.value or {}).get("related_ids", []) if pv else []
        assert str(entry.id) not in related


def test_move_db_to_non_db_preserves_other_ids_in_mirror(
    db, workspace, database_a, database_b, entry
):
    """
    Other IDs already in the mirror value must not be removed – only the
    departing entry's ID is stripped.
    """
    schema_a = repo.create_schema(
        db,
        database_id=database_a.id,
        name="Links",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_b.id),
            "direction": "bilateral",
            "mirror_property_name": "BackLinks",
        },
    )
    mirror_schema = repo.create_schema(
        db,
        database_id=database_b.id,
        name="BackLinks",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_a.id),
            "direction": "bilateral",
            "mirror_property_name": "Links",
        },
    )
    entry_b = repo.create_block(db, type="page", position=1.0, parent_id=database_b.id)
    other_entry = repo.create_block(db, type="page", position=2.0, parent_id=database_a.id)
    db.commit()

    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_a.id,
        value={"related_ids": [str(entry_b.id)]},
    )
    # entry_b is linked to both entry AND other_entry
    repo.upsert_value(
        db, page_id=entry_b.id, schema_id=mirror_schema.id,
        value={"related_ids": [str(entry.id), str(other_entry.id)]},
    )
    db.commit()

    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()

    pv = repo.get_value(db, entry_b.id, mirror_schema.id)
    related = (pv.value or {}).get("related_ids", []) if pv else []
    assert str(entry.id) not in related
    assert str(other_entry.id) in related


def test_move_db_to_non_db_unilateral_relation_not_touched(
    db, workspace, database_a, database_b, entry
):
    """
    Unilateral relations must not trigger any mirror cleanup.
    """
    schema_a = repo.create_schema(
        db,
        database_id=database_a.id,
        name="Refs",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_b.id),
            "direction": "unilateral",
        },
    )
    # Manually set a value in database_b as if it were a mirror (it isn't).
    fake_mirror = repo.create_schema(
        db, database_id=database_b.id, name="FakeMirror", type="relation", position=1.0
    )
    entry_b = repo.create_block(db, type="page", position=1.0, parent_id=database_b.id)
    db.commit()

    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema_a.id,
        value={"related_ids": [str(entry_b.id)]},
    )
    repo.upsert_value(
        db, page_id=entry_b.id, schema_id=fake_mirror.id,
        value={"related_ids": [str(entry.id)]},
    )
    db.commit()

    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()

    # The fake mirror must be untouched since the relation is unilateral.
    pv = repo.get_value(db, entry_b.id, fake_mirror.id)
    related = (pv.value or {}).get("related_ids", []) if pv else []
    assert str(entry.id) in related


# ─── move / purge: bilateral_self relation cleanup ────────────────────────────


def test_move_db_to_non_db_clears_bilateral_self_mirror(
    db, workspace, database_a, entry
):
    """
    When an entry with a bilateral_self relation leaves its database, its ID
    must be removed from the sibling entries' values in the same schema.
    """
    schema = repo.create_schema(
        db,
        database_id=database_a.id,
        name="Siblings",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_a.id),
            "direction": "bilateral_self",
        },
    )
    sibling = repo.create_block(db, type="page", position=2.0, parent_id=database_a.id)
    db.commit()

    # entry → sibling (bilateral_self: sibling also points back)
    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema.id,
        value={"related_ids": [str(sibling.id)]},
    )
    repo.upsert_value(
        db, page_id=sibling.id, schema_id=schema.id,
        value={"related_ids": [str(entry.id)]},
    )
    db.commit()

    service.move(db, entry.id, new_parent_id=workspace.id, new_position=10.0)
    db.commit()

    sibling_pv = repo.get_value(db, sibling.id, schema.id)
    related = (sibling_pv.value or {}).get("related_ids", []) if sibling_pv else []
    assert str(entry.id) not in related


def test_purge_clears_bilateral_self_mirror(
    db, workspace, database_a, entry
):
    """
    When an entry with a bilateral_self relation is hard-deleted, its ID must
    be removed from the sibling entries' values in the same schema.
    """
    schema = repo.create_schema(
        db,
        database_id=database_a.id,
        name="Siblings",
        type="relation",
        position=1.0,
        config={
            "target_database_id": str(database_a.id),
            "direction": "bilateral_self",
        },
    )
    sibling = repo.create_block(db, type="page", position=2.0, parent_id=database_a.id)
    db.commit()

    repo.upsert_value(
        db, page_id=entry.id, schema_id=schema.id,
        value={"related_ids": [str(sibling.id)]},
    )
    repo.upsert_value(
        db, page_id=sibling.id, schema_id=schema.id,
        value={"related_ids": [str(entry.id)]},
    )
    db.commit()

    service.soft_delete(db, entry.id)
    db.commit()
    affected = service.purge(db, entry.id)
    db.commit()

    sibling_pv = repo.get_value(db, sibling.id, schema.id)
    related = (sibling_pv.value or {}).get("related_ids", []) if sibling_pv else []
    assert str(entry.id) not in related
    # bilateral_self does not add to affected set (own DB broadcast handled by caller)
    assert str(database_a.id) not in affected


# ─── deep_duplicate ───────────────────────────────────────────────────────────


def test_deep_duplicate_returns_new_block_with_different_id(db, workspace, page):
    dup = service.deep_duplicate(db, page.id, parent_id=workspace.id, position=2.0)
    assert dup.id != page.id


def test_deep_duplicate_copies_type(db, workspace, page):
    dup = service.deep_duplicate(db, page.id, parent_id=workspace.id, position=2.0)
    assert dup.type == page.type


def test_deep_duplicate_copies_content(db, workspace):
    block = repo.create_block(
        db, type="paragraph", position=1.0, parent_id=workspace.id,
        content={"text": "hello world"},
    )
    db.commit()
    dup = service.deep_duplicate(db, block.id, parent_id=workspace.id, position=2.0)
    assert dup.content == {"text": "hello world"}


def test_deep_duplicate_copies_icon(db, workspace, page):
    repo.update_block(db, page, icon="mdi:star")
    db.commit()
    dup = service.deep_duplicate(db, page.id, parent_id=workspace.id, position=2.0)
    assert dup.icon == "mdi:star"


def test_deep_duplicate_copies_cover(db, workspace, page):
    repo.update_block(db, page, cover="gradient:blue")
    db.commit()
    dup = service.deep_duplicate(db, page.id, parent_id=workspace.id, position=2.0)
    assert dup.cover == "gradient:blue"


def test_deep_duplicate_uses_given_position(db, workspace, page):
    dup = service.deep_duplicate(db, page.id, parent_id=workspace.id, position=99.0)
    assert dup.position == 99.0


def test_deep_duplicate_copies_children(db, workspace, page):
    child = repo.create_block(
        db, type="paragraph", position=1.0, parent_id=page.id,
        content={"text": "child text"},
    )
    db.commit()

    dup = service.deep_duplicate(db, page.id, parent_id=workspace.id, position=2.0)
    db.commit()

    dup_children = repo.list_children(db, dup.id, state="active")
    assert len(dup_children) == 1
    assert dup_children[0].id != child.id
    assert dup_children[0].content == {"text": "child text"}


def test_deep_duplicate_copies_grandchildren(db, workspace, page):
    child = repo.create_block(db, type="toggle", position=1.0, parent_id=page.id)
    grandchild = repo.create_block(
        db, type="paragraph", position=1.0, parent_id=child.id,
        content={"text": "nested"},
    )
    db.commit()

    dup = service.deep_duplicate(db, page.id, parent_id=workspace.id, position=2.0)
    db.commit()

    dup_children = repo.list_children(db, dup.id, state="active")
    assert len(dup_children) == 1
    dup_grandchildren = repo.list_children(db, dup_children[0].id, state="active")
    assert len(dup_grandchildren) == 1
    assert dup_grandchildren[0].id != grandchild.id
    assert dup_grandchildren[0].content == {"text": "nested"}


def test_deep_duplicate_preserves_child_order(db, workspace, page):
    c1 = repo.create_block(
        db, type="paragraph", position=1.0, parent_id=page.id,
        content={"text": "first"},
    )
    c2 = repo.create_block(
        db, type="paragraph", position=2.0, parent_id=page.id,
        content={"text": "second"},
    )
    c3 = repo.create_block(
        db, type="paragraph", position=3.0, parent_id=page.id,
        content={"text": "third"},
    )
    db.commit()

    dup = service.deep_duplicate(db, page.id, parent_id=workspace.id, position=5.0)
    db.commit()

    dup_children = repo.list_children(db, dup.id, state="active")
    texts = [c.content["text"] for c in dup_children]
    assert texts == ["first", "second", "third"]


def test_deep_duplicate_no_children_works(db, workspace):
    block = repo.create_block(
        db, type="paragraph", position=1.0, parent_id=workspace.id,
        content={"text": "solo"},
    )
    db.commit()

    dup = service.deep_duplicate(db, block.id, parent_id=workspace.id, position=2.0)
    db.commit()

    assert dup.content == {"text": "solo"}
    assert repo.list_children(db, dup.id, state="active") == []


def test_deep_duplicate_raises_for_unknown_block(db, workspace):
    with pytest.raises(BlockNotFound):
        service.deep_duplicate(db, uuid.uuid4(), parent_id=workspace.id, position=1.0)


def test_deep_duplicate_original_is_unchanged(db, workspace, page):
    child = repo.create_block(
        db, type="paragraph", position=1.0, parent_id=page.id,
        content={"text": "original child"},
    )
    db.commit()

    service.deep_duplicate(db, page.id, parent_id=workspace.id, position=2.0)
    db.commit()

    db.refresh(page)
    original_children = repo.list_children(db, page.id, state="active")
    assert len(original_children) == 1
    assert original_children[0].id == child.id


# ─── deep_duplicate: synched_origin / synched_mirror ─────────────────────────


def test_deep_duplicate_synched_origin_creates_mirror(db, workspace, page):
    """Duplicating a synched_origin must produce a synched_mirror, not a copy."""
    origin = repo.create_block(db, type="synched_origin", position=1.0, parent_id=page.id)
    db.commit()

    result = service.deep_duplicate(db, origin.id, parent_id=page.id, position=2.0)
    db.commit()

    assert result.type == "synched_mirror"


def test_deep_duplicate_synched_origin_mirror_has_correct_reference_id(db, workspace, page):
    """The created mirror's reference_id must point to the origin, not itself."""
    origin = repo.create_block(db, type="synched_origin", position=1.0, parent_id=page.id)
    db.commit()

    mirror = service.deep_duplicate(db, origin.id, parent_id=page.id, position=2.0)
    db.commit()

    assert mirror.reference_id == origin.id


def test_deep_duplicate_synched_origin_mirror_has_new_id(db, workspace, page):
    origin = repo.create_block(db, type="synched_origin", position=1.0, parent_id=page.id)
    db.commit()

    mirror = service.deep_duplicate(db, origin.id, parent_id=page.id, position=2.0)
    db.commit()

    assert mirror.id != origin.id


def test_deep_duplicate_synched_origin_does_not_copy_children(db, workspace, page):
    """The mirror must have no children of its own; it uses the origin's children."""
    origin = repo.create_block(db, type="synched_origin", position=1.0, parent_id=page.id)
    repo.create_block(db, type="paragraph", position=1.0, parent_id=origin.id,
                      content={"text": "shared content"})
    db.commit()

    mirror = service.deep_duplicate(db, origin.id, parent_id=page.id, position=2.0)
    db.commit()

    mirror_children = repo.list_children(db, mirror.id, state="active")
    assert mirror_children == []


def test_deep_duplicate_synched_origin_origin_children_unchanged(db, workspace, page):
    """The origin's children must not be affected by the duplication."""
    origin = repo.create_block(db, type="synched_origin", position=1.0, parent_id=page.id)
    child = repo.create_block(db, type="paragraph", position=1.0, parent_id=origin.id,
                               content={"text": "shared content"})
    db.commit()

    service.deep_duplicate(db, origin.id, parent_id=page.id, position=2.0)
    db.commit()

    origin_children = repo.list_children(db, origin.id, state="active")
    assert len(origin_children) == 1
    assert origin_children[0].id == child.id


def test_deep_duplicate_synched_origin_mirror_uses_given_position(db, workspace, page):
    origin = repo.create_block(db, type="synched_origin", position=1.0, parent_id=page.id)
    db.commit()

    mirror = service.deep_duplicate(db, origin.id, parent_id=page.id, position=77.0)
    db.commit()

    assert mirror.position == 77.0


def test_deep_duplicate_synched_mirror_creates_another_mirror(db, workspace, page):
    """Duplicating a synched_mirror must produce another synched_mirror."""
    origin = repo.create_block(db, type="synched_origin", position=1.0, parent_id=page.id)
    mirror = repo.create_block(db, type="synched_mirror", position=2.0, parent_id=page.id,
                                reference_id=origin.id, content={})
    db.commit()

    result = service.deep_duplicate(db, mirror.id, parent_id=page.id, position=3.0)
    db.commit()

    assert result.type == "synched_mirror"


def test_deep_duplicate_synched_mirror_points_to_same_origin(db, workspace, page):
    """A duplicate mirror must reference the same origin as the source mirror."""
    origin = repo.create_block(db, type="synched_origin", position=1.0, parent_id=page.id)
    mirror = repo.create_block(db, type="synched_mirror", position=2.0, parent_id=page.id,
                                reference_id=origin.id, content={})
    db.commit()

    dup_mirror = service.deep_duplicate(db, mirror.id, parent_id=page.id, position=3.0)
    db.commit()

    assert dup_mirror.reference_id == origin.id


def test_deep_duplicate_synched_mirror_copies_locked_state(db, workspace, page):
    """Duplicating a locked mirror produces another locked mirror."""
    origin = repo.create_block(db, type="synched_origin", position=1.0, parent_id=page.id)
    mirror = repo.create_block(db, type="synched_mirror", position=2.0, parent_id=page.id,
                                reference_id=origin.id, content={"locked": True})
    db.commit()

    dup_mirror = service.deep_duplicate(db, mirror.id, parent_id=page.id, position=3.0)
    db.commit()

    assert (dup_mirror.content or {}).get("locked") is True


def test_deep_duplicate_synched_mirror_has_new_id(db, workspace, page):
    origin = repo.create_block(db, type="synched_origin", position=1.0, parent_id=page.id)
    mirror = repo.create_block(db, type="synched_mirror", position=2.0, parent_id=page.id,
                                reference_id=origin.id, content={})
    db.commit()

    dup_mirror = service.deep_duplicate(db, mirror.id, parent_id=page.id, position=3.0)
    db.commit()

    assert dup_mirror.id != mirror.id


# ─── create_block owner_id ────────────────────────────────────────────────────


def test_create_block_sets_owner_id(db, workspace):
    import uuid as _uuid
    uid = _uuid.uuid4()
    block = service.create_block(
        db, type="page", parent_id=workspace.id, owner_id=uid
    )
    db.commit()
    assert block.owner_id == uid


def test_create_block_owner_id_defaults_to_none(db, workspace):
    block = service.create_block(db, type="page", parent_id=workspace.id)
    db.commit()
    assert block.owner_id is None


def test_deep_duplicate_inherits_owner_id(db, workspace):
    import uuid as _uuid
    uid = _uuid.uuid4()
    original = service.create_block(
        db, type="page", parent_id=workspace.id, owner_id=uid
    )
    db.commit()
    dup = service.deep_duplicate(db, original.id, parent_id=workspace.id, position=99.0)
    db.commit()
    assert dup.owner_id == uid


def test_deep_duplicate_uses_explicit_owner_id(db, workspace):
    import uuid as _uuid
    original_owner = _uuid.uuid4()
    new_owner = _uuid.uuid4()
    original = service.create_block(
        db, type="page", parent_id=workspace.id, owner_id=original_owner
    )
    db.commit()
    dup = service.deep_duplicate(
        db, original.id, parent_id=workspace.id, position=99.0, owner_id=new_owner
    )
    db.commit()
    assert dup.owner_id == new_owner
