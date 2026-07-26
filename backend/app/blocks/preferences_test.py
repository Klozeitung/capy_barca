"""
Tests for the block preferences HTTP endpoints.

Authentication and the workspace root come from the shared ``http_client``
fixture in conftest.py, which overrides ``get_current_user`` — the same gate
the block router uses in production.
"""
import uuid

import pytest

from app.blocks.models import WORKSPACE_ROOT_ID


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
