import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

import app.session.session as s
from app.main import app
from app.users.repository import create_user

# /api/register issues the same Secure session cookie as /api/login, and a
# cookie jar never returns a Secure cookie over plain HTTP. The client is
# therefore addressed over HTTPS, matching the only deployment the installer
# produces and keeping the suite independent of the ambient DEBUG value.
_BASE_URL = "https://testserver"

client = TestClient(app, base_url=_BASE_URL)

# Long enough for the minimum the initial administrator now has to meet.
VALID_PASSWORD = "geheim-1234"


# isolated_db (autouse) from conftest.py handles per-test DB isolation.
# No .env patching is needed – configured state is derived from the users table.


def cookie_attributes(response) -> set:
    """
    Return the lower-cased attribute names of the response's Set-Cookie header.

    Parsing the attributes instead of substring-matching the raw header keeps
    the assertion immune to a random token value that happens to contain an
    attribute name.
    """
    header = response.headers["set-cookie"]
    return {part.strip().split("=")[0].lower() for part in header.split(";")[1:]}


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Signup is throttled and every test here shares one client address."""
    from app.security.limiter import limiter

    limiter._storage.reset()
    yield


@pytest.fixture(autouse=True)
def clean_cookie_jar():
    """
    Start and end every test with an empty jar on the module-level client.

    The client is shared across the module, so without this a cookie set by
    one test would leak into the next and make results order-dependent.
    """
    client.cookies.clear()
    yield
    client.cookies.clear()


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
    response = client.post("/api/register", json={"username": "capybarca", "password": VALID_PASSWORD})
    assert response.status_code == 200


def test_register_response_body():
    response = client.post("/api/register", json={"username": "capybarca", "password": VALID_PASSWORD})
    data = response.json()
    assert data["success"] is True
    assert data["username"] == "capybarca"
    assert data["role"] == "admin"


def test_register_sets_session_cookie():
    response = client.post("/api/register", json={"username": "capybarca", "password": VALID_PASSWORD})
    assert "session" in response.cookies


def test_register_cookie_has_secure_flag_outside_debug(monkeypatch):
    """
    Register must issue the same cookie attributes as login.

    Both endpoints share ``set_session_cookie``; this asserts the shared
    helper is actually reached from this call site.
    """
    monkeypatch.delenv("DEBUG", raising=False)
    response = client.post("/api/register", json={"username": "capybarca", "password": VALID_PASSWORD})
    attributes = cookie_attributes(response)
    assert "secure" in attributes
    assert "httponly" in attributes


def test_register_cookie_omits_secure_flag_in_debug_mode(monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    response = client.post("/api/register", json={"username": "capybarca", "password": VALID_PASSWORD})
    assert "secure" not in cookie_attributes(response)


def test_register_session_cookie_is_valid():
    client.post("/api/register", json={"username": "capybarca", "password": VALID_PASSWORD})
    verify_resp = client.get("/api/verify")
    assert verify_resp.status_code == 200


def test_register_blocked_when_already_configured():
    with s.SessionLocal() as db:
        create_user(db, "capybarca", "geheim", role="admin")
        db.commit()
    response = client.post("/api/register", json={"username": "other", "password": VALID_PASSWORD})
    assert response.status_code == 403


def test_register_returns_422_on_empty_username():
    response = client.post("/api/register", json={"username": "", "password": VALID_PASSWORD})
    assert response.status_code == 422


def test_register_returns_422_on_empty_password():
    response = client.post("/api/register", json={"username": "capybarca", "password": ""})
    assert response.status_code == 422


# ─── Password rules ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("password", ["", "a", "kurz", "1234567"])
def test_register_rejects_a_password_below_the_minimum(password):
    """
    The initial administrator used to be the one account creatable with a
    single character, while every other route already required eight.
    """
    resp = client.post(
        "/api/register", json={"username": "capybarca", "password": password}
    )
    assert resp.status_code == 422


def test_register_accepts_a_password_at_the_minimum():
    resp = client.post("/api/register", json={"username": "capybarca", "password": "12345678"})
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "password",
    [
        "a" * 73,      # one byte past what bcrypt accepts
        "ä" * 40,      # forty characters, eighty bytes
        "🙂" * 20,      # twenty characters, eighty bytes
    ],
)
def test_register_rejects_a_password_bcrypt_would_refuse(password):
    """
    Without the bound these reach bcrypt, which raises rather than returning a
    mismatch, and the caller gets a 500 out of an ordinary registration.
    """
    resp = client.post(
        "/api/register", json={"username": "capybarca", "password": password}
    )
    assert resp.status_code == 422


def test_register_accepts_a_password_at_the_byte_ceiling():
    resp = client.post("/api/register", json={"username": "capybarca", "password": "a" * 72})
    assert resp.status_code == 200


# ─── Signup ───────────────────────────────────────────────────────────────────


@pytest.fixture
def signup_enabled(monkeypatch):
    """Enable self-registration and seed the admin that signup requires."""
    monkeypatch.setenv("ALLOW_NEW_USERS", "true")
    with s.SessionLocal() as db:
        create_user(db, "capybarca", "geheim", role="admin")
        db.commit()
    yield


def test_signup_rejects_an_over_long_password(signup_enabled):
    resp = client.post("/api/signup", json={"username": "newbie", "password": "a" * 73})
    assert resp.status_code == 422


def test_signup_rate_limit_blocks_after_threshold(signup_enabled):
    """
    Self-registration hands out accounts, so it is throttled like the login
    route. The requests below are rejected on their merits; what is asserted
    is that the budget is consumed regardless.
    """
    import app.setup_router as sr

    threshold = int(sr._SIGNUP_RATE_LIMIT.split("/")[0])
    for i in range(threshold):
        client.post("/api/signup", json={"username": f"newbie{i}", "password": VALID_PASSWORD})
    resp = client.post("/api/signup", json={"username": "last", "password": VALID_PASSWORD})
    assert resp.status_code == 429


# ─── Backup script download ───────────────────────────────────────────────────


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
        c = TestClient(app, base_url=_BASE_URL)
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
        c = TestClient(app, base_url=_BASE_URL)
        c.cookies.set("session", "test-token")
        response = c.get("/api/backup/script")
    assert response.status_code == 404
