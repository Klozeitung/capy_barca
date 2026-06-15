import pytest
from fastapi.testclient import TestClient

import app.session.session as s
from app.main import app
from app.users.repository import create_user

client = TestClient(app)


@pytest.fixture(autouse=True)
def test_user():
    """Insert a test user into the isolated DB before each test."""
    with s.SessionLocal() as db:
        create_user(db, "capybarca", "geheim", role="admin")
        db.commit()


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset the in-memory rate-limit counter before each test."""
    from app.security.limiter import limiter
    limiter._storage.reset()
    yield


# ─── Login ───────────────────────────────────────────────────────────────────

def test_login_returns_200_on_valid_credentials():
    response = client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    assert response.status_code == 200


def test_login_response_body_on_success():
    response = client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    data = response.json()
    assert data["success"] is True
    assert data["username"] == "capybarca"
    assert data["role"] == "admin"


def test_login_response_includes_date_format():
    response = client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    assert response.json()["date_format"] == "DD.MM.YYYY"


def test_login_sets_session_cookie():
    response = client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    assert "session" in response.cookies


def test_login_returns_401_on_wrong_password():
    response = client.post("/api/login", json={"username": "capybarca", "password": "falsch"})
    assert response.status_code == 401


def test_login_returns_401_on_wrong_username():
    response = client.post("/api/login", json={"username": "falsch", "password": "geheim"})
    assert response.status_code == 401


def test_login_returns_422_on_missing_field():
    response = client.post("/api/login", json={"username": "capybarca"})
    assert response.status_code == 422


def test_login_rate_limit_blocks_after_threshold():
    """After 5 attempts /api/login must return 429 Too Many Requests."""
    for _ in range(5):
        client.post("/api/login", json={"username": "x", "password": "x"})
    response = client.post("/api/login", json={"username": "x", "password": "x"})
    assert response.status_code == 429


def test_login_rate_limit_resets_after_storage_clear():
    """After resetting the limiter, further attempts are allowed."""
    from app.security.limiter import limiter
    for _ in range(5):
        client.post("/api/login", json={"username": "x", "password": "x"})
    limiter._storage.reset()
    response = client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    assert response.status_code == 200


# ─── Verify ──────────────────────────────────────────────────────────────────

def test_verify_returns_401_without_cookie():
    response = client.get("/api/verify")
    assert response.status_code == 401


def test_verify_returns_200_after_login():
    client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    response = client.get("/api/verify")
    assert response.status_code == 200
    data = response.json()
    assert data["authenticated"] is True
    assert data["username"] == "capybarca"
    assert data["role"] == "admin"


def test_verify_response_includes_date_format():
    client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    data = client.get("/api/verify").json()
    assert data["date_format"] == "DD.MM.YYYY"


def test_verify_returns_401_with_invalid_token():
    client.cookies.set("session", "ungueltig")
    response = client.get("/api/verify")
    assert response.status_code == 401


# ─── Logout ──────────────────────────────────────────────────────────────────

def test_logout_returns_200():
    client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    response = client.post("/api/logout")
    assert response.status_code == 200


def test_verify_returns_401_after_logout():
    client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    client.post("/api/logout")
    response = client.get("/api/verify")
    assert response.status_code == 401
