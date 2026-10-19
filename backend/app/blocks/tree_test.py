"""
Tests for page tree loading.

The HTTP behaviour is covered in ``router_test.py``; these tests pin which
blocks the tree contains, which parents it reports as complete, how
permissions prune it, which mentions it resolves and what it costs.
"""
import uuid
from datetime import datetime, timezone

import pytest

import app.database.database as db_module
from app.blocks import repository as repo
from app.blocks import tree as page_tree
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


def _user(db, role) -> User:
    user = User(
        id=uuid.uuid4(), username=f"user_{uuid.uuid4().hex[:8]}",
        password_hash="x", role=role, is_active=True,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def admin(db) -> User:
    return _user(db, "admin")


@pytest.fixture
def member(db) -> User:
    return _user(db, "member")


def _block(db, parent, type_="paragraph", position=1.0, text=None, **kwargs) -> Block:
    content = {"text": text} if text is not None else {}
    parent_id = parent.id if isinstance(parent, Block) else parent
    block = repo.create_block(
        db, type=type_, position=position, parent_id=parent_id, content=content, **kwargs,
    )
    db.commit()
    return block


def _page(db) -> Block:
    return _block(db, WORKSPACE_ROOT_ID, "page")


def _private(db, block, owner=None) -> None:
    block.owner_id = owner.id if owner else uuid.uuid4()
    perm_repo.set_permission(db, block.id, "private", [])
    db.commit()


def _ids(blocks) -> set[uuid.UUID]:
    return {b.id for b in blocks}


# ─── Contents ────────────────────────────────────────────────────────────────


def test_nested_containers_are_loaded_whatever_their_fold_state(db, admin):
    page = _page(db)
    layout = _block(db, page, "layout")
    column = _block(db, layout, "column")
    toggle = _block(db, column, "toggle")
    inner = _block(db, toggle, "heading_2", text="Heading A")

    tree = page_tree.load_tree(db, page.id, admin)

    assert _ids(tree.blocks) == {layout.id, column.id, toggle.id, inner.id}
    assert set(tree.loaded) == {page.id, layout.id, column.id, toggle.id}


def test_subpages_and_databases_are_not_entered(db, admin):
    page = _page(db)
    subpage = _block(db, page, "page")
    _block(db, subpage, "paragraph")
    database = _block(db, page, "database")
    _block(db, database, "page")

    tree = page_tree.load_tree(db, page.id, admin)

    assert _ids(tree.blocks) == {subpage.id, database.id}
    assert tree.loaded == [page.id]


def test_children_keep_their_position_order(db, admin):
    page = _page(db)
    second = _block(db, page, position=2.0)
    first = _block(db, page, position=1.0)
    third = _block(db, page, position=3.0)

    tree = page_tree.load_tree(db, page.id, admin)

    assert [b.id for b in tree.blocks] == [first.id, second.id, third.id]


def test_children_at_equal_positions_keep_their_creation_order(db, admin):
    """Equal positions are ordered like repository.list_children orders them."""
    from datetime import timedelta

    page = _page(db)
    layout = _block(db, page, "layout")
    start = datetime(2000, 7, 28, tzinfo=timezone.utc)
    columns = [_block(db, layout, "column", position=1.0) for _ in range(3)]
    for minutes, column in zip((3, 1, 2), columns):
        column.created_at = start + timedelta(minutes=minutes)
    db.commit()

    tree = page_tree.load_tree(db, page.id, admin)

    loaded = [b.id for b in tree.blocks if b.parent_id == layout.id]
    assert loaded == [columns[1].id, columns[2].id, columns[0].id]
    assert loaded == [c.id for c in repo.list_children(db, layout.id)]


def test_trashed_blocks_are_left_out_with_their_content(db, admin):
    page = _page(db)
    toggle = _block(db, page, "toggle")
    _block(db, toggle)
    toggle.state = "trash"
    db.commit()

    tree = page_tree.load_tree(db, page.id, admin)

    assert tree.blocks == []
    assert tree.loaded == [page.id]


def test_mirror_shows_the_children_of_its_origin(db, admin):
    source_page = _page(db)
    origin = _block(db, source_page, "synched_origin")
    shared = _block(db, origin, text="Shared text")
    page = _page(db)
    mirror = _block(db, page, "synched_mirror", reference_id=origin.id)

    tree = page_tree.load_tree(db, page.id, admin)

    assert _ids(tree.blocks) == {mirror.id, shared.id}
    assert origin.id in tree.loaded


def test_mirror_inside_its_own_origin_does_not_loop(db, admin):
    page = _page(db)
    origin = _block(db, page, "synched_origin")
    mirror = _block(db, origin, "synched_mirror", reference_id=origin.id)

    tree = page_tree.load_tree(db, page.id, admin)

    assert _ids(tree.blocks) == {origin.id, mirror.id}
    assert tree.loaded.count(origin.id) == 1


def test_nesting_beyond_the_limit_is_left_for_later(db, admin, monkeypatch):
    monkeypatch.setattr(page_tree, "MAX_LEVELS", 2)
    page = _page(db)
    outer = _block(db, page, "toggle")
    inner = _block(db, outer, "toggle")
    _block(db, inner)

    tree = page_tree.load_tree(db, page.id, admin)

    assert _ids(tree.blocks) == {outer.id, inner.id}
    assert inner.id not in tree.loaded


# ─── Permissions ─────────────────────────────────────────────────────────────


def test_unreadable_block_is_pruned_with_its_subtree(db, member):
    page = _page(db)
    hidden = _block(db, page, "toggle")
    _block(db, hidden)
    _private(db, hidden)
    visible = _block(db, page, position=2.0)

    tree = page_tree.load_tree(db, page.id, member)

    assert _ids(tree.blocks) == {visible.id}
    assert hidden.id not in tree.loaded


def test_own_private_block_stays_in_the_tree(db, member):
    page = _page(db)
    own = _block(db, page, "toggle")
    child = _block(db, own)
    _private(db, own, owner=member)

    tree = page_tree.load_tree(db, page.id, member)

    assert _ids(tree.blocks) == {own.id, child.id}


def test_mirror_of_an_unreadable_origin_is_not_expanded(db, member):
    source_page = _page(db)
    _private(db, source_page)
    origin = _block(db, source_page, "synched_origin")
    _block(db, origin)
    page = _page(db)
    mirror = _block(db, page, "synched_mirror", reference_id=origin.id)

    tree = page_tree.load_tree(db, page.id, member)

    assert _ids(tree.blocks) == {mirror.id}
    assert origin.id not in tree.loaded


# ─── Mentions ────────────────────────────────────────────────────────────────


def test_mentions_are_resolved_once_and_only_when_readable(db, member):
    other = _page(db)
    hidden = _page(db)
    _private(db, hidden)
    missing = uuid.uuid4()
    page = _page(db)
    sibling = _block(db, page, "page", position=1.0)
    _block(db, page, position=2.0, text=(
        f"See @{other.id}| and @{hidden.id}| and @{missing}| and @{other.id}| and @{sibling.id}|"
    ))

    tree = page_tree.load_tree(db, page.id, member)

    assert [b.id for b in tree.mentioned] == [other.id]


def test_mention_ids_ignore_malformed_tokens():
    first = uuid.uuid4()
    second = uuid.uuid4()
    blocks = [
        Block(content={"text": f"@{first}| text @{first}| @not-an-id| @{second}"}),
        Block(content={"text": f"@{second}|"}),
        Block(content=None),
        Block(content={"text": 42}),
    ]

    assert page_tree.mention_ids(blocks) == [first, second]


# ─── Cost ────────────────────────────────────────────────────────────────────


def _layouts(db, page, count):
    for i in range(count):
        layout = _block(db, page, "layout", position=float(i))
        for j in range(2):
            column = _block(db, layout, "column", position=float(j))
            _block(db, column, text=f"Text {i}-{j}")


def test_cost_does_not_grow_with_the_number_of_blocks(db, member, count_queries):
    small_page = _page(db)
    _layouts(db, small_page, 2)
    large_page = _page(db)
    _layouts(db, large_page, 20)

    small_id, large_id = small_page.id, large_page.id

    page_tree.load_tree(db, small_id, member)
    with count_queries() as small:
        small_tree = page_tree.load_tree(db, small_id, member)
    with count_queries() as large:
        large_tree = page_tree.load_tree(db, large_id, member)

    assert len(small_tree.blocks) == 2 * 5
    assert len(large_tree.blocks) == 20 * 5
    assert large.count == small.count
