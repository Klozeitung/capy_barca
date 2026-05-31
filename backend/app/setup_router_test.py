import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

import app.session.session as s
from app.main import app
from app.users.repository import create_user

client = TestClient(app)


# isolated_db (autouse) from conftest.py handles per-test DB isolation.
# No .env patching is needed – configured state is derived from the users table.


def test_setup_status_returns_not_configured():
    response = client.get("/api/setup-status")
    assert response.status_code == 200
    assert response.json()["configured"] is False


def test_setup_status_returns_configured():
    with s.SessionLocal() as db:
        create_user(db, "capybarca", "geheim", role="admin")
        db.commit()
    response = client.get("/api/setup-status")
    assert response.json()["configured"] is True


def test_register_returns_200():
    response = client.post("/api/register", json={"username": "capybarca", "password": "geheim"})
    assert response.status_code == 200


def test_register_response_body():
    response = client.post("/api/register", json={"username": "capybarca", "password": "geheim"})
    data = response.json()
    assert data["success"] is True
    assert data["username"] == "capybarca"
    assert data["role"] == "admin"


def test_register_sets_session_cookie():
    response = client.post("/api/register", json={"username": "capybarca", "password": "geheim"})
    assert "session" in response.cookies


def test_register_session_cookie_is_valid():
    client.post("/api/register", json={"username": "capybarca", "password": "geheim"})
    verify_resp = client.get("/api/verify")
    assert verify_resp.status_code == 200


def test_register_blocked_when_already_configured():
    with s.SessionLocal() as db:
        create_user(db, "capybarca", "geheim", role="admin")
        db.commit()
    response = client.post("/api/register", json={"username": "other", "password": "other"})
    assert response.status_code == 403


def test_register_returns_422_on_empty_username():
    response = client.post("/api/register", json={"username": "", "password": "geheim"})
    assert response.status_code == 422


def test_register_returns_422_on_empty_password():
    response = client.post("/api/register", json={"username": "capybarca", "password": ""})
    assert response.status_code == 422


# ─── Backup-Script-Download ───────────────────────────────────────────────────


@pytest.fixture
def backup_script_path(tmp_path, monkeypatch):
    """Provide a temporary backup.sh and point the router module at it."""
    import app.setup_router as sr

    script = tmp_path / "backup.sh"
    script.write_text(
        '#!/bin/bash\nREMOTE_HOST="YOUR_TAILSCALE_HOSTNAME_HERE"\n'
        'REMOTE_USER="YOUR_SSH_USERNAME_HERE"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sr, "_BACKUP_SCRIPT_PATH", script)
    return script


@pytest.fixture
def auth_client(backup_script_path):
    """TestClient with a session cookie that passes validate_token."""
    with patch("app.setup_router.validate_token", return_value=True):
        c = TestClient(app)
        c.cookies.set("session", "test-token")
        yield c


def test_backup_script_requires_auth(backup_script_path):
    response = client.get("/api/backup/script")
    assert response.status_code == 401


def test_backup_script_returns_200(auth_client):
    with patch("app.setup_router.validate_token", return_value=True):
        response = auth_client.get("/api/backup/script")
    assert response.status_code == 200


def test_backup_script_content_type(auth_client):
    with patch("app.setup_router.validate_token", return_value=True):
        response = auth_client.get("/api/backup/script")
    assert "text/x-sh" in response.headers["content-type"]


def test_backup_script_content_disposition(auth_client):
    with patch("app.setup_router.validate_token", return_value=True):
        response = auth_client.get("/api/backup/script")
    assert 'filename="backup.sh"' in response.headers["content-disposition"]


def test_backup_script_fills_remote_host_from_env(auth_client, monkeypatch):
    monkeypatch.setenv("TAILSCALE_HOSTNAME", "myserver.example.com")
    with patch("app.setup_router.validate_token", return_value=True):
        response = auth_client.get("/api/backup/script")
    assert 'REMOTE_HOST="myserver.example.com"' in response.text
    assert "YOUR_TAILSCALE_HOSTNAME_HERE" not in response.text


def test_backup_script_keeps_placeholder_when_no_tailscale_hostname(auth_client, monkeypatch):
    monkeypatch.delenv("TAILSCALE_HOSTNAME", raising=False)
    with patch("app.setup_router.validate_token", return_value=True):
        response = auth_client.get("/api/backup/script")
    assert "YOUR_TAILSCALE_HOSTNAME_HERE" in response.text


def test_backup_script_returns_404_when_file_missing(monkeypatch):
    import app.setup_router as sr
    from pathlib import Path

    monkeypatch.setattr(sr, "_BACKUP_SCRIPT_PATH", Path("/nonexistent/backup.sh"))
    with patch("app.setup_router.validate_token", return_value=True):
        c = TestClient(app)
        c.cookies.set("session", "test-token")
        response = c.get("/api/backup/script")
    assert response.status_code == 404
