"""
Tests for the view service layer.

The HTTP behaviour is covered in ``views_router_test.py``; these tests pin
the rules underneath: the default layout, partial merges, positions, the
property lifecycle hooks and how a page is assembled.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import app.database.database as db_module
from app.blocks import repository as repo
from app.blocks import views
from app.blocks.models import WORKSPACE_ROOT_ID, Block, DatabaseView, DatabaseViewSelection
from app.permissions import repository as perm_repo
from app.users.model import User


# ─── Fixtures and helpers ────────────────────────────────────────────────────


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def db():
    with db_module.SessionLocal() as session:
        session.add(Block(
            id=WORKSPACE_ROOT_ID, type="workspace", position=0.0, state="active",
            content={"title": "Workspace"}, created_at=_now(), updated_at=_now(),
        ))
        session.flush()
        perm_repo.set_permission(session, WORKSPACE_ROOT_ID, "everyone", [])
        session.commit()
        yield session


@pytest.fixture
def admin(db) -> User:
    user = User(
        id=uuid.uuid4(), username=f"user_{uuid.uuid4().hex[:8]}",
        password_hash="x", role="admin", is_active=True,
    )
    db.add(user)
    db.commit()
    return user


def _database(db) -> Block:
    database = repo.create_block(db, type="database", position=1.0, parent_id=WORKSPACE_ROOT_ID)
    db.commit()
    return database


def _entry(db, database, title) -> Block:
    entry = repo.create_block(
        db, type="page", position=1.0, parent_id=database.id, content={"title": title},
    )
    db.commit()
    return entry


def _schema(db, database, name, type_="text", config=None):
    schema = repo.create_schema(
        db, database_id=database.id, name=name, type=type_, position=1.0, config=config,
    )
    db.commit()
    return schema


def _titles(page) -> list[str]:
    return [e["content"]["title"] for e in page["entries"]]


# ─── Default layout ──────────────────────────────────────────────────────────


def test_default_settings_list_every_column_and_hide_system_ones(db):
    database = _database(db)
    text = _schema(db, database, "Text")
    created = _schema(db, database, "Created", "created_time")

    settings = views.default_settings(repo.list_schemas(db, database.id))

    assert settings["colOrder"][0] == views.NAME_COL_KEY
    assert set(settings["colOrder"][1:]) == {str(text.id), str(created.id)}
    assert settings["hiddenColumns"] == [str(created.id)]


def test_ensure_views_is_idempotent(db):
    database = _database(db)

    first = views.ensure_views(db, database.id)
    second = views.ensure_views(db, database.id)
    db.commit()

    assert [v.id for v in first] == [v.id for v in second]
    readonly = [s for s in repo.list_schemas(db, database.id) if s.type in views.READONLY_SCHEMA_TYPES]
    assert len(readonly) == len({s.type for s in readonly})


def test_selection_of_a_deleted_view_falls_back_to_the_first(db):
    database = _database(db)
    [first] = views.ensure_views(db, database.id)
    second = views.create_view(db, database.id)
    views.select_view(db, database.id, database.id, second.id)
    views.delete_view(db, database.id, second)
    db.commit()

    remaining = views.list_views(db, database.id)

    assert db.get(DatabaseViewSelection, database.id) is None
    assert views.selected_view_id(db, remaining, database.id) == first.id


# ─── Mutations ───────────────────────────────────────────────────────────────


def test_duplicate_of_the_last_view_goes_to_the_end(db):
    database = _database(db)
    [first] = views.ensure_views(db, database.id)

    copy = views.duplicate_view(db, database.id, first, name="Copy")

    assert copy.position == first.position + 1.0
    assert copy.settings == first.settings
    assert copy.settings is not first.settings


def test_invalid_view_type_leaves_the_view_unchanged(db):
    database = _database(db)
    [view] = views.ensure_views(db, database.id)

    with pytest.raises(HTTPException) as exc:
        views.update_view(db, view, name="View A", view_type="spiral")

    assert exc.value.status_code == 422
    assert view.name == ""
    assert view.view_type == "table"


def test_merged_settings_are_validated_as_a_whole(db):
    database = _database(db)
    [view] = views.ensure_views(db, database.id)
    before = dict(view.settings)

    with pytest.raises(HTTPException):
        views.update_view(db, view, settings={"columnAggregations": {"x": "median"}})

    assert view.settings == before


def test_order_with_a_repeated_view_is_refused(db):
    database = _database(db)
    [first] = views.ensure_views(db, database.id)
    second = views.create_view(db, database.id)

    with pytest.raises(HTTPException) as exc:
        views.reorder_views(db, database.id, [first.id, first.id, second.id])

    assert exc.value.status_code == 422


def test_linked_block_of_another_database_is_no_placement(db):
    database = _database(db)
    other = _database(db)
    linked = repo.create_block(
        db, type="linked_database", position=2.0, parent_id=WORKSPACE_ROOT_ID,
        reference_id=other.id,
    )
    db.commit()

    with pytest.raises(HTTPException) as exc:
        views.require_placement(db, database.id, linked.id)

    assert exc.value.status_code == 422


def test_deleting_a_linked_block_drops_its_selection(db):
    database = _database(db)
    [view] = views.ensure_views(db, database.id)
    linked = repo.create_block(
        db, type="linked_database", position=2.0, parent_id=WORKSPACE_ROOT_ID,
        reference_id=database.id,
    )
    views.select_view(db, database.id, linked.id, view.id)
    db.commit()

    db.delete(linked)
    db.commit()

    assert db.get(DatabaseViewSelection, linked.id) is None


# ─── Property lifecycle ──────────────────────────────────────────────────────


def test_hiding_a_property_twice_does_not_repeat_it(db):
    database = _database(db)
    [view] = views.ensure_views(db, database.id)
    schema_id = uuid.uuid4()

    views.hide_schema_in_views(db, database.id, schema_id)
    views.hide_schema_in_views(db, database.id, schema_id)

    assert view.settings["hiddenColumns"].count(str(schema_id)) == 1


def test_forgetting_a_property_clears_every_reference():
    key = str(uuid.uuid4())
    other = str(uuid.uuid4())
    settings = {
        "colOrder": [views.NAME_COL_KEY, key, other],
        "hiddenColumns": [key],
        "wrapColumns": [key, other],
        "colWidths": {key: 100.0, other: 80.0},
        "columnAggregations": {key: "sum"},
        "groupBySchemaId": key,
        "calendarDateSchemaId": key,
        "stickyHeader": True,
    }

    result = views._forget_in_settings(settings, key)

    assert result == {
        "colOrder": [views.NAME_COL_KEY, other],
        "hiddenColumns": [],
        "wrapColumns": [other],
        "colWidths": {other: 80.0},
        "columnAggregations": {},
        "stickyHeader": True,
    }


def test_forgetting_a_property_keeps_groups_with_other_conditions(db):
    database = _database(db)
    first = _schema(db, database, "First")
    second = _schema(db, database, "Second")
    [view] = views.ensure_views(db, database.id)
    views.update_view(db, view, filter_groups=[
        {"conjunction": "or", "filters": [
            {"schemaId": str(first.id), "operator": "eq", "value": "a"},
            {"schemaId": str(second.id), "operator": "eq", "value": "b"},
        ]},
        {"conjunction": "and", "filters": [
            {"schemaId": str(first.id), "operator": "is_empty"},
        ]},
    ])

    views.forget_schema_in_views(db, database.id, first.id)

    assert view.filter_groups == [{"conjunction": "or", "filters": [
        {"schemaId": str(second.id), "operator": "eq", "value": "b"},
    ]}]


# ─── Rendering ───────────────────────────────────────────────────────────────


def test_conditions_on_missing_properties_are_skipped(db, admin):
    database = _database(db)
    _entry(db, database, "Entry A")
    [view] = views.ensure_views(db, database.id)
    views.update_view(
        db, view,
        filter_groups=[{"conjunction": "and", "filters": [
            {"schemaId": str(uuid.uuid4()), "operator": "eq", "value": "x"},
        ]}],
        sorts=[{"schemaId": str(uuid.uuid4()), "direction": "asc"}],
    )

    page = views.render_page(db, view, admin)

    assert _titles(page) == ["Entry A"]


def test_without_limit_every_entry_is_returned(db, admin):
    database = _database(db)
    for i in range(5):
        _entry(db, database, f"Entry {i}")
    [view] = views.ensure_views(db, database.id)

    page = views.render_page(db, view, admin, offset=3, limit=None)

    assert len(page["entries"]) == 5
    assert page["offset"] == 0
    assert page["total"] == 5


def test_offset_beyond_the_end_returns_an_empty_page(db, admin):
    database = _database(db)
    _entry(db, database, "Entry A")
    [view] = views.ensure_views(db, database.id)

    page = views.render_page(db, view, admin, offset=10, limit=5)

    assert page["entries"] == []
    assert page["total"] == 1


def test_page_without_relations_carries_empty_maps(db, admin):
    database = _database(db)
    _entry(db, database, "Entry A")
    [view] = views.ensure_views(db, database.id)

    page = views.render_page(db, view, admin)

    assert page["relations"] == {}
    assert page["key_values"] == {}


def test_page_cost_does_not_grow_with_the_page_size(db, admin, count_queries):
    database = _database(db)
    target = _database(db)
    link = _schema(db, database, "Link", "relation", {"target_database_id": str(target.id)})
    text = _schema(db, database, "Text")
    for i in range(30):
        linked = _entry(db, target, f"Linked {i}")
        entry = _entry(db, database, f"Entry {i}")
        repo.upsert_value(db, page_id=entry.id, schema_id=link.id, value={"related_ids": [str(linked.id)]})
        repo.upsert_value(db, page_id=entry.id, schema_id=text.id, value={"text": f"t{i}"})
    db.commit()
    [view] = views.ensure_views(db, database.id)
    db.commit()

    views.render_page(db, view, admin, limit=3)
    with count_queries() as small:
        small_page = views.render_page(db, view, admin, limit=3)
    with count_queries() as large:
        large_page = views.render_page(db, view, admin, limit=30)

    assert len(small_page["relations"]) == 3
    assert len(large_page["relations"]) == 30
    assert large.count == small.count


def test_page_ids_match_the_entries_of_the_page(db, admin):
    database = _database(db)
    for title in ("Entry C", "Entry A", "Entry B", "Entry D"):
        _entry(db, database, title)
    [view] = views.ensure_views(db, database.id)
    views.update_view(db, view, sorts=[{"schemaId": views.NAME_COL_KEY, "direction": "asc"}])

    page = views.render_page(db, view, admin, offset=1, limit=2, search="Entry")
    ids = views.page_entry_ids(db, view, offset=1, limit=2, search="Entry")

    assert ids["entries"] == [
        {"id": e["id"], "position": e["position"]} for e in page["entries"]
    ]
    assert ids["total"] == page["total"] == 4
    assert ids["offset"] == page["offset"] == 1
    assert _titles(page) == ["Entry B", "Entry C"]


def test_page_ids_of_an_unpaged_view_cover_every_entry(db):
    database = _database(db)
    for i in range(3):
        _entry(db, database, f"Entry {i}")
    [view] = views.ensure_views(db, database.id)
    views.update_view(db, view, view_type="calendar")

    ids = views.page_entry_ids(db, view, offset=2, limit=1)

    assert len(ids["entries"]) == 3
    assert ids["offset"] == 0


def test_page_ids_skip_the_values_and_relations_of_a_page(db, admin, count_queries):
    database = _database(db)
    target = _database(db)
    link = _schema(db, database, "Link", "relation", {"target_database_id": str(target.id)})
    for i in range(30):
        linked = _entry(db, target, f"Linked {i}")
        entry = _entry(db, database, f"Entry {i}")
        repo.upsert_value(db, page_id=entry.id, schema_id=link.id, value={"related_ids": [str(linked.id)]})
    db.commit()
    [view] = views.ensure_views(db, database.id)
    db.commit()

    views.page_entry_ids(db, view, limit=3)
    with count_queries() as small:
        views.page_entry_ids(db, view, limit=3)
    with count_queries() as large:
        views.page_entry_ids(db, view, limit=30)
    with count_queries() as page:
        views.render_page(db, view, admin, limit=30)

    assert large.count == small.count
    assert large.count < page.count


def test_deleting_the_database_removes_its_views(db):
    database = _database(db)
    views.ensure_views(db, database.id)
    db.commit()

    db.delete(database)
    db.commit()

    remaining = db.query(DatabaseView).filter(DatabaseView.database_id == database.id).count()
    assert remaining == 0


def test_forgetting_options_drops_conditions_that_test_them(db):
    database = _database(db)
    status = _schema(db, database, "Status", "select", {"options": [
        {"id": "o1", "label": "Value A"}, {"id": "o2", "label": "Value B"},
    ]})
    [view] = views.ensure_views(db, database.id)
    views.update_view(db, view, filter_groups=[
        {"conjunction": "or", "filters": [
            {"schemaId": str(status.id), "operator": "eq", "value": "o1"},
            {"schemaId": str(status.id), "operator": "eq", "value": "o2"},
        ]},
        {"conjunction": "and", "filters": [
            {"schemaId": str(status.id), "operator": "neq", "value": "o1"},
        ]},
    ])

    views.forget_options_in_views(db, database.id, status.id, {"o1"})

    assert view.filter_groups == [{"conjunction": "or", "filters": [
        {"schemaId": str(status.id), "operator": "eq", "value": "o2"},
    ]}]
