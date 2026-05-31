"""
Integration tests for the WebSocket router.

All tests exercise the /ws endpoint through FastAPI's TestClient, which
handles the async ASGI transport transparently.
"""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDisconnect

from app.main import app


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def authed_client(isolated_db):
    """TestClient with a valid session cookie pre-set."""
    with patch("app.ws.router.validate_token", return_value=True):
        client = TestClient(app)
        client.cookies.set("session", "test-token")
        yield client


# ─── Authentication ───────────────────────────────────────────────────────────


def test_ws_connects_with_valid_session(authed_client):
    with authed_client.websocket_connect("/ws"):
        pass  # clean connect + disconnect


def test_ws_rejects_missing_session(isolated_db):
    with patch("app.ws.router.validate_token", return_value=False):
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws") as ws:
                ws.receive_text()
        assert exc_info.value.code == 4401


def test_ws_rejects_invalid_session(isolated_db):
    with patch("app.ws.router.validate_token", return_value=False):
        client = TestClient(app)
        client.cookies.set("session", "bogus-token")
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws") as ws:
                ws.receive_text()
        assert exc_info.value.code == 4401


# ─── Ping / pong ──────────────────────────────────────────────────────────────


def test_ws_ping_receives_pong(authed_client):
    with authed_client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "ping"}))
        response = json.loads(ws.receive_text())
        assert response["type"] == "pong"


def test_ws_multiple_pings_receive_multiple_pongs(authed_client):
    with authed_client.websocket_connect("/ws") as ws:
        for _ in range(3):
            ws.send_text(json.dumps({"type": "ping"}))
            response = json.loads(ws.receive_text())
            assert response["type"] == "pong"


def test_ws_invalid_json_does_not_close_connection(authed_client):
    """Malformed frames must be silently ignored, not crash the handler."""
    with authed_client.websocket_connect("/ws") as ws:
        ws.send_text("this is not json")
        # connection stays alive: a subsequent ping still works
        ws.send_text(json.dumps({"type": "ping"}))
        response = json.loads(ws.receive_text())
        assert response["type"] == "pong"


def test_ws_unknown_message_type_is_ignored(authed_client):
    with authed_client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "unknown_op", "data": 42}))
        # no response expected; connection stays alive
        ws.send_text(json.dumps({"type": "ping"}))
        response = json.loads(ws.receive_text())
        assert response["type"] == "pong"
