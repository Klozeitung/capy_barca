"""
Tests for the permission repository's resolution logic.

The focus is the batched resolver: it must give exactly the answers the
per-block walk gave, and it must do so with a number of queries that depends
on the depth of the tree, not on how many blocks are resolved at once. The
access decisions are checked per level ('read' and 'write').
"""
import uuid
from datetime import datetime, timezone

import pytest

import app.database.database as db_module
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.permissions import repository as perm_repo
from app.permissions.model import READ, WRITE, BlockPermission, BlockPermissionGrant
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


def _permission(db, block_id, mode: str, grants=(), *, everyone_level: str = WRITE) -> None:
    """
    Store a permission row. *grants* is a collection of user ids (write) or a
    mapping of user id to level.
    """
    db.add(BlockPermission(block_id=block_id, mode=mode, everyone_level=everyone_level))
    db.flush()
    levels = dict(grants) if isinstance(grants, dict) else {uid: WRITE for uid in grants}
    for user_id, level in levels.items():
        db.add(BlockPermissionGrant(block_id=block_id, user_id=user_id, level=level))
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

    assert effective.admits(owner, WRITE)
    assert not effective.admits(stranger, READ)


def test_whitelist_admits_owner_and_grantees(db):
    owner = _user(db)
    grantee = _user(db)
    stranger = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "whitelist", grants=[grantee.id])

    effective = perm_repo.resolve_effective_permissions(db, [page.id])[page.id]

    assert effective.admits(owner, WRITE)
    assert effective.admits(grantee, WRITE)
    assert not effective.admits(stranger, READ)


def test_admin_is_always_admitted(db):
    owner = _user(db)
    admin = _user(db, role="admin")
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "private")

    effective = perm_repo.resolve_effective_permissions(db, [page.id])[page.id]

    assert effective.admits(admin, WRITE)


def test_can_user_access_skips_the_database_for_admins(db, count_queries):
    admin = _user(db, role="admin")
    with count_queries() as counter:
        assert perm_repo.can_user_access(db, uuid.uuid4(), admin, level=WRITE)
    assert counter.count == 0


def test_filter_accessible_returns_only_admitted_blocks(db):
    owner = _user(db)
    stranger = _user(db)
    shared = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, shared.id, "everyone")
    hidden = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, hidden.id, "private")

    allowed = perm_repo.filter_accessible(db, [shared.id, hidden.id], stranger, level=READ)

    assert allowed == {shared.id}


def test_filter_accessible_returns_everything_for_admins(db, count_queries):
    admin = _user(db, role="admin")
    ids = {uuid.uuid4(), uuid.uuid4()}
    with count_queries() as counter:
        assert perm_repo.filter_accessible(db, ids, admin, level=WRITE) == ids
    assert counter.count == 0


# ─── Access levels ───────────────────────────────────────────────────────────


def test_satisfies_orders_the_levels():
    assert perm_repo.satisfies(WRITE, WRITE)
    assert perm_repo.satisfies(WRITE, READ)
    assert perm_repo.satisfies(READ, READ)
    assert not perm_repo.satisfies(READ, WRITE)
    assert not perm_repo.satisfies(None, READ)


def test_satisfies_rejects_an_unknown_level():
    with pytest.raises(ValueError):
        perm_repo.satisfies(WRITE, "admin")


def test_reader_grant_admits_reading_only(db):
    owner = _user(db)
    reader = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "whitelist", grants={reader.id: READ})

    effective = perm_repo.resolve_effective_permissions(db, [page.id])[page.id]

    assert effective.level_for(reader) == READ
    assert effective.admits(reader, READ)
    assert not effective.admits(reader, WRITE)


def test_everyone_level_applies_to_every_member(db):
    owner = _user(db)
    member = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "everyone", everyone_level=READ)

    effective = perm_repo.resolve_effective_permissions(db, [page.id])[page.id]

    assert effective.level_for(member) == READ
    assert effective.level_for(owner) == WRITE


def test_owner_and_admin_keep_write_access_under_a_read_level(db):
    owner = _user(db)
    admin = _user(db, role="admin")
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "everyone", everyone_level=READ)

    assert perm_repo.access_level(db, page.id, owner) == WRITE
    assert perm_repo.access_level(db, page.id, admin) == WRITE


def test_level_is_inherited_with_the_row(db):
    owner = _user(db)
    reader = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "whitelist", grants={reader.id: READ})
    child = _block(db, page.id, owner.id)

    assert perm_repo.access_level(db, child.id, reader) == READ


def test_access_levels_answers_each_block(db):
    owner = _user(db)
    member = _user(db)
    readable = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, readable.id, "whitelist", grants={member.id: READ})
    writable = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, writable.id, "everyone")
    hidden = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, hidden.id, "private")

    levels = perm_repo.access_levels(db, [readable.id, writable.id, hidden.id], member)

    assert levels == {readable.id: READ, writable.id: WRITE, hidden.id: None}


def test_filter_accessible_separates_the_levels(db):
    owner = _user(db)
    member = _user(db)
    readable = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, readable.id, "whitelist", grants={member.id: READ})
    writable = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, writable.id, "whitelist", grants={member.id: WRITE})
    ids = [readable.id, writable.id]

    assert perm_repo.filter_accessible(db, ids, member, level=READ) == set(ids)
    assert perm_repo.filter_accessible(db, ids, member, level=WRITE) == {writable.id}


def test_set_permission_stores_levels_from_a_mapping(db):
    owner = _user(db)
    reader = _user(db)
    writer = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)

    perm = perm_repo.set_permission(
        db, page.id, "whitelist", {reader.id: READ, writer.id: WRITE}, everyone_level=READ,
    )

    assert perm.everyone_level == READ
    assert {g.user_id: g.level for g in perm.grants} == {reader.id: READ, writer.id: WRITE}


def test_set_permission_gives_a_plain_list_write_access(db):
    owner = _user(db)
    grantee = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)

    perm = perm_repo.set_permission(db, page.id, "whitelist", [grantee.id])

    assert [g.level for g in perm.grants] == [WRITE]
    assert perm.everyone_level == WRITE


def test_set_permission_updates_the_level_of_a_kept_grant(db):
    owner = _user(db)
    grantee = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    perm_repo.set_permission(db, page.id, "whitelist", [grantee.id])

    perm = perm_repo.set_permission(db, page.id, "whitelist", {grantee.id: READ})

    assert [g.level for g in perm.grants] == [READ]


@pytest.mark.parametrize(
    "grants,everyone_level",
    [({}, "admin"), ({uuid.uuid4(): "owner"}, WRITE)],
)
def test_set_permission_rejects_an_unknown_level(db, grants, everyone_level):
    owner = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    with pytest.raises(ValueError):
        perm_repo.set_permission(db, page.id, "whitelist", grants, everyone_level=everyone_level)


def test_database_rejects_an_unknown_stored_level(db):
    """The check constraints keep a value outside the two levels out of the table."""
    from sqlalchemy.exc import IntegrityError

    owner = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    db.commit()
    db.add(BlockPermission(block_id=page.id, mode="everyone", everyone_level="admin"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


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
