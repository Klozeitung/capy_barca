"""
Tests for relation resolution.

Covers the single-database form behind ``resolve-titles`` and the page form
behind view pages and single entries: which ids resolve, how timelines and
keyed relations are handled, how permissions apply, and that the cost does
not grow with the number of entries.
"""
import uuid
from datetime import datetime, timezone

import pytest

import app.database.database as db_module
from app.blocks import repository as repo
from app.blocks import resolution
from app.blocks.models import WORKSPACE_ROOT_ID, Block
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


def _user(db, role="admin") -> User:
    user = User(
        id=uuid.uuid4(), username=f"user_{uuid.uuid4().hex[:8]}",
        password_hash="x", role=role, is_active=True,
    )
    db.add(user)
    db.commit()
    return user


def _database(db, owner=None, mode=None) -> Block:
    database = repo.create_block(db, type="database", position=1.0, parent_id=WORKSPACE_ROOT_ID)
    if owner is not None:
        database.owner_id = owner.id
    db.flush()
    if mode is not None:
        perm_repo.set_permission(db, database.id, mode, [])
    db.commit()
    return database


def _entry(db, database, title, block_type="page", state="active") -> Block:
    entry = repo.create_block(
        db, type=block_type, position=1.0, parent_id=database.id, content={"title": title},
    )
    entry.state = state
    db.commit()
    return entry


def _relation(db, database, target, name="Link", config=None):
    cfg = {"target_database_id": str(target.id)}
    cfg.update(config or {})
    schema = repo.create_schema(
        db, database_id=database.id, name=name, type="relation", position=1.0, config=cfg,
    )
    db.commit()
    return schema


# ─── linked_ids ──────────────────────────────────────────────────────────────


def test_linked_ids_reads_a_plain_value():
    assert resolution.linked_ids({"related_ids": ["a", "b"]}) == ["a", "b"]


def test_linked_ids_collects_every_timeline_period_once():
    value = {"_timeline": {
        "2000-01-01→2000-12-31": {"related_ids": ["a", "b"]},
        "2001-01-01→": {"related_ids": ["b", "c"]},
    }}
    assert resolution.linked_ids(value) == ["a", "b", "c"]


@pytest.mark.parametrize("value", [None, "text", {"related_ids": None}, {"_timeline": None}])
def test_linked_ids_tolerates_unusable_values(value):
    assert resolution.linked_ids(value) == []


# ─── resolve_titles ──────────────────────────────────────────────────────────


def test_resolve_titles_returns_active_children_only(db):
    target = _database(db)
    other = _database(db)
    kept = _entry(db, target, "Entry A")
    trashed = _entry(db, target, "Entry B", state="trash")
    template = _entry(db, target, "Entry C", block_type="entry_template")
    foreign = _entry(db, other, "Entry D")

    titles = resolution.resolve_titles(
        db, target.id, [kept.id, trashed.id, template.id, foreign.id, uuid.uuid4()],
    )

    assert [(t.id, t.title) for t in titles] == [(kept.id, "Entry A")]


def test_resolve_titles_carries_the_requested_value(db):
    target = _database(db)
    entry = _entry(db, target, "Entry A")
    key = repo.create_schema(db, database_id=target.id, name="Key", type="number", position=1.0)
    repo.upsert_value(db, page_id=entry.id, schema_id=key.id, value={"number": 7})
    db.commit()

    [title] = resolution.resolve_titles(db, target.id, [entry.id], key.id)

    assert title.value == {"number": 7}


def test_resolve_titles_ignores_a_value_property_of_another_database(db):
    target = _database(db)
    other = _database(db)
    entry = _entry(db, target, "Entry A")
    foreign_key = repo.create_schema(
        db, database_id=other.id, name="Key", type="number", position=1.0,
    )
    db.commit()

    [title] = resolution.resolve_titles(db, target.id, [entry.id], foreign_key.id)

    assert title.value is None


# ─── resolve_relations ───────────────────────────────────────────────────────


def test_relations_resolve_across_target_databases(db):
    admin = _user(db)
    source = _database(db)
    first_target = _database(db)
    second_target = _database(db)
    a = _entry(db, first_target, "Entry A")
    b = _entry(db, second_target, "Entry B")
    first = _relation(db, source, first_target, name="First")
    second = _relation(db, source, second_target, name="Second")

    resolved = resolution.resolve_relations(
        db,
        repo.list_schemas(db, source.id),
        [{str(first.id): {"related_ids": [str(a.id)]}, str(second.id): {"related_ids": [str(b.id)]}}],
        admin,
    )

    assert resolved.titles[str(a.id)].title == "Entry A"
    assert resolved.titles[str(b.id)].database_id == second_target.id


def test_an_id_from_the_wrong_database_does_not_resolve(db):
    """A relation only resolves entries of the database it points into."""
    admin = _user(db)
    source = _database(db)
    target = _database(db)
    elsewhere = _database(db)
    stray = _entry(db, elsewhere, "Entry A")
    link = _relation(db, source, target)

    resolved = resolution.resolve_relations(
        db, repo.list_schemas(db, source.id),
        [{str(link.id): {"related_ids": [str(stray.id)]}}], admin,
    )

    assert resolved.titles == {}


def test_parent_item_resolves_within_the_same_database(db):
    admin = _user(db)
    database = _database(db)
    parent = _entry(db, database, "Entry A")
    repo.ensure_readonly_schemas(db, database.id)
    db.commit()
    parent_item = next(s for s in repo.list_schemas(db, database.id) if s.type == "parent_item")

    resolved = resolution.resolve_relations(
        db, repo.list_schemas(db, database.id),
        [{str(parent_item.id): {"related_ids": [str(parent.id)]}}], admin,
    )

    assert resolved.titles[str(parent.id)].database_id == database.id


def test_unreadable_target_database_resolves_nothing(db):
    owner = _user(db, role="member")
    stranger = _user(db, role="member")
    source = _database(db)
    hidden = _database(db, owner=owner, mode="private")
    entry = _entry(db, hidden, "Entry A")
    link = _relation(db, source, hidden)

    resolved = resolution.resolve_relations(
        db, repo.list_schemas(db, source.id),
        [{str(link.id): {"related_ids": [str(entry.id)]}}], stranger,
    )

    assert resolved.titles == {}


def test_keyed_relation_carries_key_values(db):
    admin = _user(db)
    source = _database(db)
    target = _database(db)
    a = _entry(db, target, "Entry A")
    b = _entry(db, target, "Entry B")
    key = repo.create_schema(db, database_id=target.id, name="When", type="date", position=1.0)
    repo.upsert_value(db, page_id=a.id, schema_id=key.id, value={"start": "2000-07-28"})
    db.commit()
    link = _relation(db, source, target, config={
        "keying": {"enabled": True, "key_property_id": str(key.id)},
    })

    resolved = resolution.resolve_relations(
        db, repo.list_schemas(db, source.id),
        [{str(link.id): {"related_ids": [str(a.id), str(b.id)]}}], admin,
    )

    assert resolved.key_values == {str(key.id): {str(a.id): {"start": "2000-07-28"}}}


def test_keying_on_a_property_of_another_database_yields_no_values(db):
    admin = _user(db)
    source = _database(db)
    target = _database(db)
    elsewhere = _database(db)
    a = _entry(db, target, "Entry A")
    foreign_key = repo.create_schema(
        db, database_id=elsewhere.id, name="When", type="date", position=1.0,
    )
    db.commit()
    link = _relation(db, source, target, config={
        "keying": {"enabled": True, "key_property_id": str(foreign_key.id)},
    })

    resolved = resolution.resolve_relations(
        db, repo.list_schemas(db, source.id),
        [{str(link.id): {"related_ids": [str(a.id)]}}], admin,
    )

    assert str(a.id) in resolved.titles
    assert resolved.key_values == {}


def test_without_relation_properties_nothing_is_queried(db, count_queries):
    admin = _user(db)
    database = _database(db)
    text = repo.create_schema(db, database_id=database.id, name="Text", type="text", position=1.0)
    db.commit()
    schemas = repo.list_schemas(db, database.id)
    values = [{str(text.id): {"text": "x"}}]

    with count_queries() as counter:
        resolved = resolution.resolve_relations(db, schemas, values, admin)

    assert resolved.titles == {}
    assert counter.count == 0


def test_cost_does_not_grow_with_the_number_of_entries(db, count_queries):
    member = _user(db, role="member")
    source = _database(db)
    target = _database(db)
    key = repo.create_schema(db, database_id=target.id, name="When", type="date", position=1.0)
    db.commit()
    link = _relation(db, source, target, config={
        "keying": {"enabled": True, "key_property_id": str(key.id)},
    })
    target_ids = [str(_entry(db, target, f"Entry {i}").id) for i in range(40)]
    link_key = str(link.id)
    schemas = repo.list_schemas(db, source.id)
    few_values = [{link_key: {"related_ids": [target_ids[i]]}} for i in range(4)]
    many_values = [{link_key: {"related_ids": [target_ids[i]]}} for i in range(40)]

    # Warm up: the first call refreshes the expired user row, which is not
    # part of the cost being compared.
    resolution.resolve_relations(db, schemas, few_values, member)
    with count_queries() as few:
        resolution.resolve_relations(db, schemas, few_values, member)
    with count_queries() as many:
        resolution.resolve_relations(db, schemas, many_values, member)

    assert many.count == few.count
