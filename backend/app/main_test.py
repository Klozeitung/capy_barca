import uuid

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_app_is_initialized():
    assert app.title == "CapyBarca API"
    assert app.version == "0.13.2"


def test_health_returns_200():
    response = client.get("/health")
    assert response.status_code == 200


def test_health_response_body():
    response = client.get("/health")
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "capybarca"


def test_login_route_is_registered():
    # Fehlende Pflichtfelder liefern 422 – das beweist, dass die Route existiert
    response = client.post("/api/login", json={})
    assert response.status_code == 422


def test_cors_header_present_for_allowed_origin():
    response = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_header_absent_for_unknown_origin():
    response = client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in response.headers


def test_ws_route_is_registered():
    """
    Verify the /ws endpoint exists. Without a valid session the server
    closes with code 4401; the route being absent would raise a different
    error entirely.
    """
    from starlette.testclient import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws") as ws:
            ws.receive_text()
    assert exc_info.value.code == 4401


def test_automations_route_is_registered():
    """
    Verify /api/automations is registered.  Without auth it must return 401,
    not 404 (which would mean the router was never included).
    """
    response = client.get("/api/automations")
    assert response.status_code == 401


def test_comments_route_is_registered():
    """
    Verify /api/blocks/{id}/comments is registered.  Without auth it must
    return 401, not 404 (which would mean the router was never included).
    """
    response = client.get(f"/api/blocks/{uuid.uuid4()}/comments")
    assert response.status_code == 401
