"""
Integration tests for the WebSocket router.

All tests exercise the /ws endpoint through FastAPI's TestClient, which
handles the async ASGI transport transparently.

``validate_token`` is patched to return a UUID rather than a bare ``True``:
the router now passes its return value on to the connection manager as the
account owning the connection, so the stub has to produce what the real
function produces.
"""
import json
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDisconnect

from app.main import app
from app.ws.manager import manager


# ─── Fixtures ─────────────────────────────────────────────────────────────────


SESSION_USER_ID = uuid.uuid4()


@pytest.fixture
def authed_client(isolated_db):
    """TestClient with a valid session cookie pre-set."""
    with patch("app.ws.router.validate_token", return_value=SESSION_USER_ID):
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


# ─── Connection registry ──────────────────────────────────────────────────────


def test_ws_registers_the_session_account(authed_client):
    """
    The manager has to learn whose connection this is during the handshake.

    The session cookie is not available afterwards, and the broadcaster cannot
    decide what a connection may receive without knowing the account behind it.
    """
    with authed_client.websocket_connect("/ws"):
        assert manager.user_ids() == {SESSION_USER_ID}


def test_ws_forgets_the_account_on_disconnect(authed_client):
    with authed_client.websocket_connect("/ws"):
        pass
    assert manager.user_ids() == set()


def test_ws_rejected_connection_is_not_registered(isolated_db):
    """A refused handshake must leave no trace in the registry."""
    with patch("app.ws.router.validate_token", return_value=None):
        client = TestClient(app)
        client.cookies.set("session", "bogus-token")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws") as ws:
                ws.receive_text()
    assert manager.active_count == 0
