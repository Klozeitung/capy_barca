"""
Tests for the permission repository's resolution logic.

The focus is the batched resolver: it must give exactly the answers the
per-block walk gave, and it must do so with a number of queries that depends
on the depth of the tree, not on how many blocks are resolved at once.
"""
import uuid
from datetime import datetime, timezone

import pytest

import app.database.database as db_module
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.permissions import repository as perm_repo
from app.permissions.model import BlockPermission, BlockPermissionGrant
from app.users.model import User


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _user(db, role: str = "member") -> User:
    user = User(
        id=uuid.uuid4(),
        username=f"user_{uuid.uuid4().hex[:8]}",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _block(db, parent_id, owner_id=None, block_type: str = "page") -> Block:
    now = datetime.now(timezone.utc)
    block = Block(
        id=uuid.uuid4(),
        parent_id=parent_id,
        type=block_type,
        position=0.0,
        state="active",
        content={"title": "Block"},
        owner_id=owner_id,
        created_at=now,
        updated_at=now,
    )
    db.add(block)
    db.flush()
    return block


def _permission(db, block_id, mode: str, grants=()) -> None:
    db.add(BlockPermission(block_id=block_id, mode=mode))
    db.flush()
    for user_id in grants:
        db.add(BlockPermissionGrant(block_id=block_id, user_id=user_id))
    db.flush()


@pytest.fixture
def db():
    with db_module.SessionLocal() as session:
        now = datetime.now(timezone.utc)
        session.add(Block(
            id=WORKSPACE_ROOT_ID,
            parent_id=None,
            type="workspace",
            position=0.0,
            state="active",
            content={"title": "Workspace"},
            created_at=now,
            updated_at=now,
        ))
        session.flush()
        _permission(session, WORKSPACE_ROOT_ID, "everyone")
        session.commit()
        yield session


# ─── Inheritance ─────────────────────────────────────────────────────────────


def test_block_inherits_the_nearest_explicit_row(db):
    owner = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "private")
    child = _block(db, page.id, owner.id)
    grandchild = _block(db, child.id, owner.id)

    resolved = perm_repo.resolve_effective_permissions(db, [child.id, grandchild.id])

    assert resolved[child.id].mode == "private"
    assert resolved[grandchild.id].mode == "private"
    assert resolved[grandchild.id].owner_id == owner.id


def test_owner_is_taken_from_the_block_that_carries_the_row(db):
    page_owner = _user(db)
    child_owner = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, page_owner.id)
    _permission(db, page.id, "private")
    child = _block(db, page.id, child_owner.id)

    resolved = perm_repo.resolve_effective_permissions(db, [child.id])

    assert resolved[child.id].owner_id == page_owner.id


def test_explicit_row_on_the_block_itself_wins(db):
    owner = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "private")
    child = _block(db, page.id, owner.id)
    _permission(db, child.id, "everyone")

    resolved = perm_repo.resolve_effective_permissions(db, [child.id])

    assert resolved[child.id].mode == "everyone"


def test_grants_are_carried_in_stored_order(db):
    owner = _user(db)
    first = _user(db)
    second = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "whitelist", grants=[first.id, second.id])

    resolved = perm_repo.resolve_effective_permissions(db, [page.id])

    assert set(resolved[page.id].grants) == {first.id, second.id}


def test_unknown_block_resolves_to_everyone(db):
    unknown = uuid.uuid4()
    resolved = perm_repo.resolve_effective_permissions(db, [unknown])
    assert resolved[unknown].mode == "everyone"
    assert resolved[unknown].owner_id is None


def test_empty_request_returns_empty_result_without_queries(db, count_queries):
    with count_queries() as counter:
        assert perm_repo.resolve_effective_permissions(db, []) == {}
    assert counter.count == 0


def test_circular_parent_links_terminate(db):
    """Corrupted data must not loop: a cycle without rows resolves to everyone."""
    first = _block(db, WORKSPACE_ROOT_ID)
    second = _block(db, first.id)
    db.commit()
    # Close the cycle behind the ORM's back; the FK allows it.
    first.parent_id = second.id
    db.commit()

    resolved = perm_repo.resolve_effective_permissions(db, [second.id])

    assert resolved[second.id].mode == "everyone"


def test_single_block_form_matches_the_batched_form(db):
    owner = _user(db)
    grantee = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "whitelist", grants=[grantee.id])
    child = _block(db, page.id, owner.id)

    mode, owner_id, grants = perm_repo.resolve_effective_permission(db, child.id)

    assert (mode, owner_id, grants) == ("whitelist", owner.id, [grantee.id])


# ─── Access decisions ────────────────────────────────────────────────────────


def test_private_admits_only_the_owner(db):
    owner = _user(db)
    stranger = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "private")

    effective = perm_repo.resolve_effective_permissions(db, [page.id])[page.id]

    assert effective.admits(owner)
    assert not effective.admits(stranger)


def test_whitelist_admits_owner_and_grantees(db):
    owner = _user(db)
    grantee = _user(db)
    stranger = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "whitelist", grants=[grantee.id])

    effective = perm_repo.resolve_effective_permissions(db, [page.id])[page.id]

    assert effective.admits(owner)
    assert effective.admits(grantee)
    assert not effective.admits(stranger)


def test_admin_is_always_admitted(db):
    owner = _user(db)
    admin = _user(db, role="admin")
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "private")

    effective = perm_repo.resolve_effective_permissions(db, [page.id])[page.id]

    assert effective.admits(admin)


def test_can_user_access_skips_the_database_for_admins(db, count_queries):
    admin = _user(db, role="admin")
    with count_queries() as counter:
        assert perm_repo.can_user_access(db, uuid.uuid4(), admin)
    assert counter.count == 0


def test_filter_accessible_returns_only_admitted_blocks(db):
    owner = _user(db)
    stranger = _user(db)
    shared = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, shared.id, "everyone")
    hidden = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, hidden.id, "private")

    allowed = perm_repo.filter_accessible(db, [shared.id, hidden.id], stranger)

    assert allowed == {shared.id}


def test_filter_accessible_returns_everything_for_admins(db, count_queries):
    admin = _user(db, role="admin")
    ids = {uuid.uuid4(), uuid.uuid4()}
    with count_queries() as counter:
        assert perm_repo.filter_accessible(db, ids, admin) == ids
    assert counter.count == 0


# ─── Query cost ──────────────────────────────────────────────────────────────


def _page_with_children(db, count: int) -> list[uuid.UUID]:
    owner = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "private")
    section = _block(db, page.id, owner.id)
    children = [_block(db, section.id, owner.id).id for _ in range(count)]
    db.commit()
    return children


def test_query_count_does_not_grow_with_the_number_of_siblings(db, count_queries):
    """
    Siblings share their parent chain, so resolving fifty of them must cost
    the same number of queries as resolving five.
    """
    few = _page_with_children(db, 5)
    many = _page_with_children(db, 50)

    with count_queries() as few_counter:
        perm_repo.resolve_effective_permissions(db, few)
    with count_queries() as many_counter:
        perm_repo.resolve_effective_permissions(db, many)

    assert many_counter.count == few_counter.count


def test_query_count_follows_the_depth_of_the_tree(db, count_queries):
    """Children sit two levels below the explicit row: at most three levels walked."""
    children = _page_with_children(db, 10)

    with count_queries() as counter:
        perm_repo.resolve_effective_permissions(db, children)

    # Two queries per level, one more for the grants of the row found.
    assert counter.count <= 3 * 2 + 1
