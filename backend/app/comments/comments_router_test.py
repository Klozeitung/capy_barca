"""
Tests for the comments router.

Uses the shared ``http_client`` fixture from conftest.py, which provides a
fully authenticated TestClient via FastAPI dependency overrides (the same
mechanism as automations_router_test, database_router_test, etc.).

The ``isolated_db`` fixture (autouse) ensures a clean in-memory SQLite
database for each test.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import app.session.session as s
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.comments.comments_models import Comment
from app.main import app
from app.permissions import repository as perm_repo
from app.session.deps import get_current_user
from app.users.model import User

# Module-level unauthenticated client for 401 checks.
# Created once at import time (no lifespan churn between tests).
anon_client = TestClient(app)


# ─── Authorization helpers ────────────────────────────────────────────────────
#
# The shared http_client authenticates as an admin, who bypasses the permission
# layer entirely. Anything testing the permission rules needs a member, its own
# client, and blocks seeded straight into the database.


def _make_user(role: str = "member") -> User:
    user = User(
        id=uuid.uuid4(),
        username=f"user_{uuid.uuid4().hex[:8]}",
        password_hash="x",
        role=role,
        is_active=True,
    )
    with s.SessionLocal() as db:
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
    return user


def _seed_block(owner_id=None, mode: str = "private", grants=()) -> str:
    block_id = uuid.uuid4()
    with s.SessionLocal() as db:
        db.merge(Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0, state="active"))
        block = Block(
            id=block_id,
            parent_id=WORKSPACE_ROOT_ID,
            type="page",
            position=1.0,
            state="active",
        )
        block.owner_id = owner_id
        db.add(block)
        db.flush()
        perm_repo.set_permission(db, block_id, mode, list(grants))
        db.commit()
    return str(block_id)


def _seed_comment(block_id: str, author_id, text: str = "seeded") -> str:
    comment_id = uuid.uuid4()
    with s.SessionLocal() as db:
        db.add(
            Comment(
                id=comment_id,
                block_id=uuid.UUID(block_id),
                author_id=author_id,
                text=text,
            )
        )
        db.commit()
    return str(comment_id)


def _comment_text(comment_id: str):
    with s.SessionLocal() as db:
        row = db.get(Comment, uuid.UUID(comment_id))
        return row.text if row is not None else None


@pytest.fixture
def client_factory(isolated_db):
    """Build a TestClient authenticated as a specific account."""
    def _make(user: User) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: user
        client = TestClient(app)
        client.cookies.set("session", "test-token")
        return client

    yield _make
    app.dependency_overrides.clear()


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def page_id(http_client):
    """Create a fresh page block and return its ID."""
    resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# ─── GET /api/blocks/{block_id}/comments ──────────────────────────────────────


def test_list_comments_returns_empty_list(http_client, page_id):
    r = http_client.get(f"/api/blocks/{page_id}/comments")
    assert r.status_code == 200
    assert r.json() == []


def test_list_comments_unknown_block_returns_404(http_client):
    r = http_client.get(f"/api/blocks/{uuid.uuid4()}/comments")
    assert r.status_code == 404


# ─── POST /api/blocks/{block_id}/comments ─────────────────────────────────────


def test_create_comment_returns_201(http_client, page_id):
    r = http_client.post(
        f"/api/blocks/{page_id}/comments",
        json={"text": "Hello world"},
    )
    assert r.status_code == 201


def test_create_comment_response_contains_text(http_client, page_id):
    r = http_client.post(
        f"/api/blocks/{page_id}/comments",
        json={"text": "Test comment"},
    )
    assert r.json()["text"] == "Test comment"


def test_create_comment_response_contains_block_id(http_client, page_id):
    r = http_client.post(
        f"/api/blocks/{page_id}/comments",
        json={"text": "Another comment"},
    )
    assert r.json()["block_id"] == page_id


def test_create_comment_response_contains_author_id(http_client, page_id):
    r = http_client.post(
        f"/api/blocks/{page_id}/comments",
        json={"text": "Authored comment"},
    )
    assert r.json()["author_id"] is not None


def test_create_comment_empty_text_returns_422(http_client, page_id):
    r = http_client.post(
        f"/api/blocks/{page_id}/comments",
        json={"text": "   "},
    )
    assert r.status_code == 422


def test_create_comment_unknown_block_returns_404(http_client):
    r = http_client.post(
        f"/api/blocks/{uuid.uuid4()}/comments",
        json={"text": "Should fail"},
    )
    assert r.status_code == 404


def test_list_comments_returns_created_comment(http_client, page_id):
    http_client.post(
        f"/api/blocks/{page_id}/comments",
        json={"text": "Visible comment"},
    )
    r = http_client.get(f"/api/blocks/{page_id}/comments")
    texts = [c["text"] for c in r.json()]
    assert "Visible comment" in texts


def test_list_comments_ordered_oldest_first(http_client, page_id):
    http_client.post(f"/api/blocks/{page_id}/comments", json={"text": "First"})
    http_client.post(f"/api/blocks/{page_id}/comments", json={"text": "Second"})
    r = http_client.get(f"/api/blocks/{page_id}/comments")
    texts = [c["text"] for c in r.json()]
    assert texts == ["First", "Second"]


# ─── PATCH /api/blocks/{block_id}/comments/{comment_id} ───────────────────────


def test_update_comment_returns_200(http_client, page_id):
    cid = http_client.post(
        f"/api/blocks/{page_id}/comments", json={"text": "Original"}
    ).json()["id"]
    r = http_client.patch(
        f"/api/blocks/{page_id}/comments/{cid}",
        json={"text": "Updated"},
    )
    assert r.status_code == 200


def test_update_comment_persists_new_text(http_client, page_id):
    cid = http_client.post(
        f"/api/blocks/{page_id}/comments", json={"text": "Old text"}
    ).json()["id"]
    http_client.patch(
        f"/api/blocks/{page_id}/comments/{cid}",
        json={"text": "New text"},
    )
    r = http_client.get(f"/api/blocks/{page_id}/comments")
    texts = [c["text"] for c in r.json()]
    assert "New text" in texts
    assert "Old text" not in texts


def test_update_comment_unknown_id_returns_404(http_client, page_id):
    r = http_client.patch(
        f"/api/blocks/{page_id}/comments/{uuid.uuid4()}",
        json={"text": "X"},
    )
    assert r.status_code == 404


def test_update_comment_wrong_block_returns_404(http_client, page_id):
    """A comment belonging to one block must not be editable via a different block_id."""
    other_page = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    ).json()["id"]
    cid = http_client.post(
        f"/api/blocks/{page_id}/comments", json={"text": "Belongs to page_id"}
    ).json()["id"]
    r = http_client.patch(
        f"/api/blocks/{other_page}/comments/{cid}",
        json={"text": "Hijack attempt"},
    )
    assert r.status_code == 404


# ─── DELETE /api/blocks/{block_id}/comments/{comment_id} ──────────────────────


def test_delete_comment_returns_204(http_client, page_id):
    cid = http_client.post(
        f"/api/blocks/{page_id}/comments", json={"text": "To delete"}
    ).json()["id"]
    r = http_client.delete(f"/api/blocks/{page_id}/comments/{cid}")
    assert r.status_code == 204


def test_delete_comment_no_longer_listed(http_client, page_id):
    cid = http_client.post(
        f"/api/blocks/{page_id}/comments", json={"text": "Bye"}
    ).json()["id"]
    http_client.delete(f"/api/blocks/{page_id}/comments/{cid}")
    r = http_client.get(f"/api/blocks/{page_id}/comments")
    ids = [c["id"] for c in r.json()]
    assert cid not in ids


def test_delete_comment_unknown_id_returns_404(http_client, page_id):
    r = http_client.delete(f"/api/blocks/{page_id}/comments/{uuid.uuid4()}")
    assert r.status_code == 404


# ─── Auth guard ────────────────────────────────────────────────────────────────


def test_comments_route_requires_authentication():
    """Without dependency overrides the endpoint must return 401."""
    r = anon_client.get(f"/api/blocks/{uuid.uuid4()}/comments")
    assert r.status_code == 401


# ─── Block access ─────────────────────────────────────────────────────────────


def test_list_comments_on_foreign_private_block_returns_404(client_factory):
    """A read must not distinguish "not yours" from "does not exist"."""
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member)
    assert client.get(f"/api/blocks/{block_id}/comments").status_code == 404


def test_create_comment_on_foreign_private_block_returns_403(client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    client = client_factory(member)
    r = client.post(f"/api/blocks/{block_id}/comments", json={"text": "Intruding"})
    assert r.status_code == 403


def test_update_comment_on_foreign_private_block_returns_403(client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    comment_id = _seed_comment(block_id, author_id=member.id, text="Mine")
    client = client_factory(member)
    r = client.patch(
        f"/api/blocks/{block_id}/comments/{comment_id}", json={"text": "Edited"}
    )
    assert r.status_code == 403
    assert _comment_text(comment_id) == "Mine"


def test_delete_comment_on_foreign_private_block_returns_403(client_factory):
    """Authoring the comment does not grant access to the block it hangs on."""
    member = _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    comment_id = _seed_comment(block_id, author_id=member.id)
    client = client_factory(member)
    assert client.delete(
        f"/api/blocks/{block_id}/comments/{comment_id}"
    ).status_code == 403
    assert _comment_text(comment_id) is not None


def test_member_with_access_may_list(client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=member.id, mode="private")
    _seed_comment(block_id, author_id=member.id, text="Visible")
    client = client_factory(member)
    r = client.get(f"/api/blocks/{block_id}/comments")
    assert r.status_code == 200
    assert [c["text"] for c in r.json()] == ["Visible"]


def test_whitelisted_member_may_create(client_factory):
    member = _make_user()
    block_id = _seed_block(
        owner_id=uuid.uuid4(), mode="whitelist", grants=[member.id]
    )
    client = client_factory(member)
    r = client.post(f"/api/blocks/{block_id}/comments", json={"text": "Allowed"})
    assert r.status_code == 201


# ─── Comment ownership ────────────────────────────────────────────────────────


def test_author_may_edit_own_comment(client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=member.id, mode="everyone")
    comment_id = _seed_comment(block_id, author_id=member.id, text="Original")
    client = client_factory(member)
    r = client.patch(
        f"/api/blocks/{block_id}/comments/{comment_id}", json={"text": "Revised"}
    )
    assert r.status_code == 200
    assert _comment_text(comment_id) == "Revised"


def test_author_may_delete_own_comment(client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=member.id, mode="everyone")
    comment_id = _seed_comment(block_id, author_id=member.id)
    client = client_factory(member)
    assert client.delete(
        f"/api/blocks/{block_id}/comments/{comment_id}"
    ).status_code == 204


def test_non_author_may_not_edit(client_factory):
    """Block access is not comment ownership."""
    author, other = _make_user(), _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="everyone")
    comment_id = _seed_comment(block_id, author_id=author.id, text="Author's words")
    client = client_factory(other)
    r = client.patch(
        f"/api/blocks/{block_id}/comments/{comment_id}", json={"text": "Rewritten"}
    )
    assert r.status_code == 403
    assert _comment_text(comment_id) == "Author's words"


def test_non_author_may_not_delete(client_factory):
    author, other = _make_user(), _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="everyone")
    comment_id = _seed_comment(block_id, author_id=author.id)
    client = client_factory(other)
    assert client.delete(
        f"/api/blocks/{block_id}/comments/{comment_id}"
    ).status_code == 403
    assert _comment_text(comment_id) is not None


def test_admin_may_edit_another_users_comment(client_factory):
    author, admin = _make_user(), _make_user(role="admin")
    block_id = _seed_block(owner_id=author.id, mode="private")
    comment_id = _seed_comment(block_id, author_id=author.id)
    client = client_factory(admin)
    r = client.patch(
        f"/api/blocks/{block_id}/comments/{comment_id}", json={"text": "Moderated"}
    )
    assert r.status_code == 200


def test_admin_may_delete_another_users_comment(client_factory):
    author, admin = _make_user(), _make_user(role="admin")
    block_id = _seed_block(owner_id=author.id, mode="private")
    comment_id = _seed_comment(block_id, author_id=author.id)
    client = client_factory(admin)
    assert client.delete(
        f"/api/blocks/{block_id}/comments/{comment_id}"
    ).status_code == 204


def test_unowned_comment_is_not_editable_by_a_member(client_factory):
    """
    author_id is nullable. A comment left behind by a deleted account must not
    become editable by whoever happens to have block access.
    """
    member = _make_user()
    block_id = _seed_block(owner_id=member.id, mode="everyone")
    comment_id = _seed_comment(block_id, author_id=None, text="Orphaned")
    client = client_factory(member)
    r = client.patch(
        f"/api/blocks/{block_id}/comments/{comment_id}", json={"text": "Claimed"}
    )
    assert r.status_code == 403
    assert _comment_text(comment_id) == "Orphaned"


def test_unowned_comment_is_editable_by_an_admin(client_factory):
    admin = _make_user(role="admin")
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
    comment_id = _seed_comment(block_id, author_id=None)
    client = client_factory(admin)
    r = client.patch(
        f"/api/blocks/{block_id}/comments/{comment_id}", json={"text": "Cleaned up"}
    )
    assert r.status_code == 200


def test_unknown_comment_still_returns_404(client_factory):
    """The ownership rule must not turn a genuine 404 into a 403."""
    member = _make_user()
    block_id = _seed_block(owner_id=member.id, mode="everyone")
    client = client_factory(member)
    r = client.patch(
        f"/api/blocks/{block_id}/comments/{uuid.uuid4()}", json={"text": "X"}
    )
    assert r.status_code == 404


# ─── can_edit ─────────────────────────────────────────────────────────────────


def test_can_edit_is_true_for_the_author(client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=member.id, mode="everyone")
    _seed_comment(block_id, author_id=member.id)
    client = client_factory(member)
    assert client.get(f"/api/blocks/{block_id}/comments").json()[0]["can_edit"] is True


def test_can_edit_is_false_for_another_member(client_factory):
    author, other = _make_user(), _make_user()
    block_id = _seed_block(owner_id=uuid.uuid4(), mode="everyone")
    _seed_comment(block_id, author_id=author.id)
    client = client_factory(other)
    assert client.get(f"/api/blocks/{block_id}/comments").json()[0]["can_edit"] is False


def test_can_edit_is_true_for_an_admin(client_factory):
    author, admin = _make_user(), _make_user(role="admin")
    block_id = _seed_block(owner_id=author.id, mode="private")
    _seed_comment(block_id, author_id=author.id)
    client = client_factory(admin)
    assert client.get(f"/api/blocks/{block_id}/comments").json()[0]["can_edit"] is True


def test_can_edit_is_false_for_an_unowned_comment(client_factory):
    member = _make_user()
    block_id = _seed_block(owner_id=member.id, mode="everyone")
    _seed_comment(block_id, author_id=None)
    client = client_factory(member)
    assert client.get(f"/api/blocks/{block_id}/comments").json()[0]["can_edit"] is False
