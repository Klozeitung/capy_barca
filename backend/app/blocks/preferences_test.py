"""
Tests for the block preferences HTTP endpoints.
"""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.session.session as s
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.main import app


@pytest.fixture(autouse=True)
def mock_auth():
    with patch("app.blocks.router.validate_token", return_value=True):
        yield


@pytest.fixture
def http_client(isolated_db):
    """TestClient with workspace root seeded and session cookie on the instance."""
    with s.SessionLocal() as db:
        block = Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0)
        db.add(block)
        db.commit()

    client = TestClient(app)
    client.cookies.set("session", "test-token")
    return client


@pytest.fixture
def page_id(http_client):
    resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    return resp.json()["id"]


# ─── GET /api/blocks/{id}/preferences/{key} ───────────────────────────────────


def test_get_preference_returns_404_when_not_set(http_client, page_id):
    r = http_client.get(f"/api/blocks/{page_id}/preferences/folded")
    assert r.status_code == 404


def test_get_preference_unknown_block_returns_404(http_client):
    r = http_client.get(f"/api/blocks/{uuid.uuid4()}/preferences/folded")
    assert r.status_code == 404


# ─── PUT /api/blocks/{id}/preferences/{key} ───────────────────────────────────


def test_put_preference_creates_value(http_client, page_id):
    r = http_client.put(
        f"/api/blocks/{page_id}/preferences/folded",
        json={"value": True},
    )
    assert r.status_code == 200
    assert r.json()["value"] is True


def test_put_preference_updates_existing_value(http_client, page_id):
    http_client.put(
        f"/api/blocks/{page_id}/preferences/folded",
        json={"value": True},
    )
    r = http_client.put(
        f"/api/blocks/{page_id}/preferences/folded",
        json={"value": False},
    )
    assert r.json()["value"] is False


def test_put_preference_then_get_returns_value(http_client, page_id):
    http_client.put(
        f"/api/blocks/{page_id}/preferences/folded",
        json={"value": True},
    )
    r = http_client.get(f"/api/blocks/{page_id}/preferences/folded")
    assert r.status_code == 200
    assert r.json()["value"] is True


# ─── GET /api/blocks/{id}/preferences ────────────────────────────────────────


def test_list_preferences_empty_for_new_block(http_client, page_id):
    r = http_client.get(f"/api/blocks/{page_id}/preferences")
    assert r.status_code == 200
    assert r.json() == []


def test_list_preferences_returns_all_keys(http_client, page_id):
    http_client.put(
        f"/api/blocks/{page_id}/preferences/folded",
        json={"value": True},
    )
    http_client.put(
        f"/api/blocks/{page_id}/preferences/custom_color",
        json={"value": "#ff0000"},
    )
    r = http_client.get(f"/api/blocks/{page_id}/preferences")
    keys = [p["key"] for p in r.json()]
    assert "folded" in keys
    assert "custom_color" in keys
