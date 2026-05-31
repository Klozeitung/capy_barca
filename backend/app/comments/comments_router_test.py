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

from app.blocks.models import WORKSPACE_ROOT_ID
from app.main import app

# Module-level unauthenticated client for 401 checks.
# Created once at import time (no lifespan churn between tests).
anon_client = TestClient(app)


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
