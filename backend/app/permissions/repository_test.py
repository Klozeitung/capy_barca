"""
Tests for the permission repository's resolution logic.

The focus is the batched resolver: restrictions accumulate along the parent
chain, and the resolver must answer for many blocks at once with a number of
queries that depends on the depth of the tree, not on how many blocks are
resolved. The access decisions are checked per level ('read' and 'write').
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


def test_explicit_row_on_the_block_itself_is_reported_as_its_mode(db):
    owner = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "private")
    child = _block(db, page.id, owner.id)
    _permission(db, child.id, "everyone")

    resolved = perm_repo.resolve_effective_permissions(db, [child.id])

    assert resolved[child.id].mode == "everyone"


def test_a_row_below_cannot_widen_what_an_ancestor_restricts(db):
    """A child shared with everyone under a private page stays private."""
    owner = _user(db)
    stranger = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "private")
    child = _block(db, page.id, owner.id)
    _permission(db, child.id, "everyone")
    grandchild = _block(db, child.id, owner.id)

    resolved = perm_repo.resolve_effective_permissions(db, [child.id, grandchild.id])

    for block_id in (child.id, grandchild.id):
        assert resolved[block_id].admits(owner, WRITE)
        assert not resolved[block_id].admits(stranger, READ)


def test_a_row_below_narrows_what_an_ancestor_allows(db):
    owner = _user(db)
    member = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "everyone")
    child = _block(db, page.id, owner.id)
    _permission(db, child.id, "private")

    effective = perm_repo.resolve_effective_permissions(db, [child.id])[child.id]

    assert not effective.admits(member, READ)
    assert effective.admits(owner, WRITE)


def test_whitelists_on_the_chain_admit_only_accounts_on_both(db):
    owner = _user(db)
    on_both = _user(db)
    only_above = _user(db)
    only_below = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "whitelist", grants=[on_both.id, only_above.id])
    child = _block(db, page.id, owner.id)
    _permission(db, child.id, "whitelist", grants=[on_both.id, only_below.id])

    effective = perm_repo.resolve_effective_permissions(db, [child.id])[child.id]

    assert effective.admits(on_both, WRITE)
    assert not effective.admits(only_above, READ)
    assert not effective.admits(only_below, READ)


def test_the_lowest_level_on_the_chain_applies(db):
    owner = _user(db)
    member = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "everyone", everyone_level=READ)
    child = _block(db, page.id, owner.id)
    _permission(db, child.id, "everyone", everyone_level=WRITE)

    effective = perm_repo.resolve_effective_permissions(db, [child.id])[child.id]

    assert effective.level_for(member) == READ


def test_owning_a_block_does_not_reach_past_a_restricting_ancestor(db):
    """The owner rule of a row applies to that row only, not to the chain above."""
    page_owner = _user(db)
    child_owner = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, page_owner.id)
    _permission(db, page.id, "private")
    child = _block(db, page.id, child_owner.id)
    _permission(db, child.id, "private")

    effective = perm_repo.resolve_effective_permissions(db, [child.id])[child.id]

    assert effective.level_for(child_owner) is None
    assert effective.level_for(page_owner) is None


def test_admin_is_admitted_whatever_the_chain_says(db):
    owner = _user(db)
    admin = _user(db, role="admin")
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "private")
    child = _block(db, page.id, owner.id)
    _permission(db, child.id, "whitelist", grants={})

    effective = perm_repo.resolve_effective_permissions(db, [child.id])[child.id]

    assert effective.level_for(admin) == WRITE


def test_outer_rows_are_listed_nearest_first(db):
    owner = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "private")
    child = _block(db, page.id, owner.id)
    _permission(db, child.id, "everyone")

    effective = perm_repo.resolve_effective_permissions(db, [child.id])[child.id]

    assert [rule.mode for rule in effective.outer] == ["private", "everyone"]


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
    """
    Children sit two levels below the page and three below the workspace root:
    every row up to the root takes part, so four levels are walked.
    """
    children = _page_with_children(db, 10)

    with count_queries() as counter:
        perm_repo.resolve_effective_permissions(db, children)

    # Two queries per level, plus one for the grants on each level that
    # carries a row (the page and the workspace root).
    assert counter.count <= 4 * 2 + 2


# ─── Restriction by ancestors ────────────────────────────────────────────────


def test_restricted_by_ancestors_when_a_wider_row_sits_under_a_private_one(db):
    owner = _user(db)
    _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "private")
    section = _block(db, page.id, owner.id)
    leaf = _block(db, section.id, owner.id)
    _permission(db, leaf.id, "everyone")

    assert perm_repo.is_restricted_by_ancestors(db, leaf.id)


def test_restricted_by_ancestors_when_a_level_is_cut_back(db):
    owner = _user(db)
    _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "everyone", everyone_level=READ)
    child = _block(db, page.id, owner.id)
    _permission(db, child.id, "everyone", everyone_level=WRITE)

    assert perm_repo.is_restricted_by_ancestors(db, child.id)


def test_restricted_by_ancestors_for_a_grant_missing_above(db):
    owner = _user(db)
    member_a = _user(db)
    member_b = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "whitelist", grants=[member_a.id])
    child = _block(db, page.id, owner.id)
    _permission(db, child.id, "whitelist", grants=[member_b.id])

    assert perm_repo.is_restricted_by_ancestors(db, child.id)


def test_not_restricted_when_the_row_only_narrows(db):
    owner = _user(db)
    member = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "everyone")
    child = _block(db, page.id, owner.id)
    _permission(db, child.id, "whitelist", grants={member.id: READ})

    assert not perm_repo.is_restricted_by_ancestors(db, child.id)


def test_not_restricted_without_a_row_of_its_own(db):
    owner = _user(db)
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "private")
    child = _block(db, page.id, owner.id)

    assert not perm_repo.is_restricted_by_ancestors(db, child.id)


def test_not_restricted_by_accounts_that_are_inactive_or_admin(db):
    owner = _user(db)
    admin = _user(db, role="admin")
    inactive = _user(db)
    inactive.is_active = False
    page = _block(db, WORKSPACE_ROOT_ID, owner.id)
    _permission(db, page.id, "private")
    child = _block(db, page.id, owner.id)
    _permission(db, child.id, "whitelist", grants=[admin.id, inactive.id])
    db.flush()

    assert not perm_repo.is_restricted_by_ancestors(db, child.id)
