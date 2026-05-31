"""
Tests for block event emission and the history/revert HTTP endpoints.

Event emission is tested at the service level to avoid coupling the tests
to the router layer. HTTP-level history and revert tests use TestClient.
"""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.session.session as s
from app.blocks import repository as repo
from app.blocks import service
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.main import app


# ─── Shared auth mock ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_auth():
    with patch("app.blocks.router.validate_token", return_value=True):
        yield


# ─── Unit-test fixtures (direct DB access) ───────────────────────────────────

@pytest.fixture
def db():
    with s.SessionLocal() as session:
        yield session


@pytest.fixture
def workspace(db):
    """Insert workspace root for unit-level tests only."""
    block = Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0)
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


@pytest.fixture
def page(db, workspace):
    block = repo.create_block(db, type="page", position=1.0, parent_id=workspace.id)
    db.commit()
    return block


# ─── HTTP-test fixture ────────────────────────────────────────────────────────

@pytest.fixture
def http_client(isolated_db):
    """
    TestClient with workspace root pre-seeded and session cookie set on the
    client instance (avoids the per-request cookies DeprecationWarning).
    isolated_db is explicitly requested to guarantee fixture ordering: the
    in-memory DB must be ready before we seed the workspace root.
    """
    with s.SessionLocal() as db:
        block = Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0)
        db.add(block)
        db.commit()

    client = TestClient(app)
    client.cookies.set("session", "test-token")
    return client


# ─── Event emission (unit level) ──────────────────────────────────────────────

def test_create_block_emits_created_event(db, workspace):
    block = service.create_block(db, type="page", parent_id=workspace.id)
    db.commit()
    events = repo.list_events(db, block.id)
    assert any(e.event_type == "created" for e in events)


def test_created_event_has_after_snapshot(db, workspace):
    block = service.create_block(db, type="page", parent_id=workspace.id)
    db.commit()
    events = repo.list_events(db, block.id)
    created = next(e for e in events if e.event_type == "created")
    assert created.after is not None
    assert created.before is None


def test_soft_delete_emits_state_changed_event(db, page):
    service.soft_delete(db, page.id)
    db.commit()
    events = repo.list_events(db, page.id)
    state_events = [e for e in events if e.event_type == "state_changed"]
    assert len(state_events) == 1
    assert state_events[0].before == {"state": "active"}
    assert state_events[0].after == {"state": "trash"}


def test_restore_emits_state_changed_event(db, page):
    service.soft_delete(db, page.id)
    db.commit()
    service.restore(db, page.id)
    db.commit()
    events = repo.list_events(db, page.id)
    restore_events = [
        e for e in events
        if e.event_type == "state_changed" and e.after == {"state": "active"}
    ]
    assert len(restore_events) == 1


def test_move_emits_moved_event(db, workspace, page):
    new_parent = repo.create_block(
        db, type="page", position=2.0, parent_id=workspace.id
    )
    db.commit()
    service.move(db, page.id, new_parent_id=new_parent.id, new_position=1.0)
    db.commit()
    events = repo.list_events(db, page.id)
    moved = [e for e in events if e.event_type == "moved"]
    assert len(moved) == 1
    assert moved[0].after["parent_id"] == str(new_parent.id)


def test_update_appearance_emits_icon_changed(db, page):
    service.update_block_appearance(db, page.id, icon="mdi:star")
    db.commit()
    events = repo.list_events(db, page.id)
    icon_events = [e for e in events if e.event_type == "icon_changed"]
    assert len(icon_events) == 1
    assert icon_events[0].after == {"icon": "mdi:star"}


def test_update_appearance_emits_cover_changed(db, page):
    service.update_block_appearance(db, page.id, cover="gradient:linear-gradient(red,blue)")
    db.commit()
    events = repo.list_events(db, page.id)
    cover_events = [e for e in events if e.event_type == "cover_changed"]
    assert len(cover_events) == 1


def test_no_icon_event_when_value_unchanged(db, page):
    service.update_block_appearance(db, page.id, icon="mdi:star")
    db.commit()
    service.update_block_appearance(db, page.id, icon="mdi:star")
    db.commit()
    events = repo.list_events(db, page.id)
    icon_events = [e for e in events if e.event_type == "icon_changed"]
    assert len(icon_events) == 1


# ─── HTTP: history endpoint ───────────────────────────────────────────────────

def test_history_returns_200(http_client):
    resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = resp.json()["id"]
    r = http_client.get(f"/api/blocks/{block_id}/history")
    assert r.status_code == 200


def test_history_contains_created_event(http_client):
    resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = resp.json()["id"]
    r = http_client.get(f"/api/blocks/{block_id}/history")
    types = [e["event_type"] for e in r.json()]
    assert "created" in types


def test_history_unknown_block_returns_404(http_client):
    r = http_client.get(f"/api/blocks/{uuid.uuid4()}/history")
    assert r.status_code == 404


# ─── HTTP: revert endpoint ────────────────────────────────────────────────────

def test_revert_icon_restores_previous_value(http_client):
    resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = resp.json()["id"]
    http_client.patch(
        f"/api/blocks/{block_id}/appearance",
        json={"icon": "mdi:star"},
    )
    history = http_client.get(f"/api/blocks/{block_id}/history").json()
    icon_event = next(e for e in history if e["event_type"] == "icon_changed")
    r = http_client.post(f"/api/blocks/{block_id}/revert/{icon_event['id']}")
    assert r.status_code == 200
    assert r.json()["icon"] is None


def test_revert_unknown_event_returns_404(http_client):
    resp = http_client.post(
        "/api/blocks",
        json={"type": "page", "parent_id": str(WORKSPACE_ROOT_ID)},
    )
    block_id = resp.json()["id"]
    r = http_client.post(f"/api/blocks/{block_id}/revert/{uuid.uuid4()}")
    assert r.status_code == 404
