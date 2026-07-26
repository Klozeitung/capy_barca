"""
Tests for the user management router (GET/POST /api/users, DELETE, PATCH).

All tests run against the in-memory SQLite DB provided by the autouse
``isolated_db`` fixture in conftest.py.

Client design
-------------
A single module-level ``TestClient(app)`` handles unauthenticated requests.
Authenticated requests use a *separate* client per fixture call so that auth
cookies are fully isolated between admin and member tests.  The fixture
client is closed with ``client.close()`` in teardown – this cleans up the
underlying httpx transport without triggering a full ASGI lifespan
start/stop cycle (which corrupts global app state for subsequent modules
such as the WebSocket router tests).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import app.session.session as s
from app.main import app
from app.users.repository import create_user

# Shared unauthenticated client for 401 checks only.
anon_client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """
    Clear the shared rate-limit counter before each test.

    The password change endpoint is throttled and every client here presents
    the same address, so without this the later tests would fail on budget the
    earlier ones spent.
    """
    from app.security.limiter import limiter

    limiter._storage.reset()
    yield


# ── Auth helpers ──────────────────────────────────────────────────────────────


def _authed_client(username: str, role: str) -> TestClient:
    """
    Create a user in the test DB, issue a session token, and return a
    TestClient pre-loaded with that token as a cookie.

    The client must be closed by the caller (fixture teardown via yield).
    ``client.close()`` shuts down the httpx transport cleanly without
    invoking the ASGI lifespan.
    """
    with s.SessionLocal() as db:
        user = create_user(db, username, "geheim", role=role)
        db.commit()
        db.refresh(user)
        token = s.create_token(user.id)

    c = TestClient(app, raise_server_exceptions=True)
    c.cookies.set("session", token)
    return c


@pytest.fixture
def admin_client():
    c = _authed_client("admin_user", "admin")
    yield c
    c.close()


@pytest.fixture
def member_client():
    c = _authed_client("member_user", "member")
    yield c
    c.close()


# ── GET /api/users/me ─────────────────────────────────────────────────────────

def test_get_me_returns_200(admin_client):
    assert admin_client.get("/api/users/me").status_code == 200


def test_get_me_returns_own_username(admin_client):
    data = admin_client.get("/api/users/me").json()
    assert data["username"] == "admin_user"
    assert data["role"] == "admin"


def test_get_me_returns_401_unauthenticated():
    assert anon_client.get("/api/users/me").status_code == 401


def test_get_me_returns_default_date_format(admin_client):
    data = admin_client.get("/api/users/me").json()
    assert data["date_format"] == "DD.MM.YYYY"


# ── PATCH /api/users/me/date-format ──────────────────────────────────────────

def test_change_date_format_returns_200(admin_client):
    response = admin_client.patch(
        "/api/users/me/date-format",
        json={"date_format": "YYYY-MM-DD"},
    )
    assert response.status_code == 200
    assert response.json()["date_format"] == "YYYY-MM-DD"


def test_change_date_format_persists(admin_client):
    admin_client.patch("/api/users/me/date-format", json={"date_format": "MM.DD.YYYY"})
    data = admin_client.get("/api/users/me").json()
    assert data["date_format"] == "MM.DD.YYYY"


def test_change_date_format_invalid_returns_422(admin_client):
    response = admin_client.patch(
        "/api/users/me/date-format",
        json={"date_format": "DD/MM/YY"},
    )
    assert response.status_code == 422


def test_change_date_format_returns_401_unauthenticated():
    response = anon_client.patch(
        "/api/users/me/date-format",
        json={"date_format": "YYYY-MM-DD"},
    )
    assert response.status_code == 401


# ── PATCH /api/users/me/password ─────────────────────────────────────────────

def test_change_password_returns_204(admin_client):
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "geheim", "new_password": "neues_pw_123"},
    )
    assert response.status_code == 204


def test_change_password_wrong_current_returns_401(admin_client):
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "falsch", "new_password": "neues_pw_123"},
    )
    assert response.status_code == 401


def test_change_password_too_short_returns_422(admin_client):
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "geheim", "new_password": "kurz"},
    )
    assert response.status_code == 422


# ── Password rules ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("password", ["a" * 73, "ä" * 40, "🙂" * 20])
def test_change_password_rejects_a_new_password_bcrypt_would_refuse(
    admin_client, password
):
    """
    bcrypt raises past 72 bytes instead of reporting a mismatch, so these used
    to surface as a 500. The last two are the reason the bound counts bytes:
    both are well under 72 characters.
    """
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "geheim", "new_password": password},
    )
    assert response.status_code == 422


def test_change_password_rejects_an_over_long_current_password(admin_client):
    """The field that is only ever verified needs the same bound."""
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "a" * 73, "new_password": "neues_pw_123"},
    )
    assert response.status_code == 422


def test_change_password_accepts_a_new_password_at_the_byte_ceiling(admin_client):
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "geheim", "new_password": "a" * 72},
    )
    assert response.status_code == 204


def test_change_password_accepts_a_short_current_password(admin_client):
    """
    No minimum on the current password. The account in this fixture predates
    the minimum, and requiring eight here would leave it unable to move off
    its short password at all.
    """
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "geheim", "new_password": "neues_pw_123"},
    )
    assert response.status_code == 204


def test_create_user_rejects_an_over_long_password(admin_client):
    response = admin_client.post(
        "/api/users",
        json={"username": "new_member", "password": "a" * 73, "role": "member"},
    )
    assert response.status_code == 422


def test_admin_reset_rejects_an_over_long_password(admin_client):
    with s.SessionLocal() as db:
        user = create_user(db, "reset_target", "geheim", role="member")
        db.commit()
        user_id = user.id
    response = admin_client.patch(
        f"/api/users/{user_id}/password", json={"new_password": "a" * 73}
    )
    assert response.status_code == 422


def test_change_password_rate_limit_blocks_after_threshold(admin_client):
    """
    The endpoint verifies the current password, which makes it an oracle for
    anyone holding a stolen session. Throttled for the same reason login is.
    """
    import app.users.router as users_router_module

    threshold = int(users_router_module._PASSWORD_CHANGE_RATE_LIMIT.split("/")[0])
    for _ in range(threshold):
        admin_client.patch(
            "/api/users/me/password",
            json={"current_password": "falsch", "new_password": "neues_pw_123"},
        )
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "falsch", "new_password": "neues_pw_123"},
    )
    assert response.status_code == 429


# ── GET /api/users ────────────────────────────────────────────────────────────

def test_list_users_returns_200_for_admin(admin_client):
    assert admin_client.get("/api/users").status_code == 200


def test_list_users_returns_403_for_member(member_client):
    assert member_client.get("/api/users").status_code == 403


def test_list_users_returns_401_unauthenticated():
    assert anon_client.get("/api/users").status_code == 401


def test_list_users_contains_created_users(admin_client):
    with s.SessionLocal() as db:
        create_user(db, "extra_user", "geheim", role="member")
        db.commit()
    usernames = [u["username"] for u in admin_client.get("/api/users").json()]
    assert "admin_user" in usernames
    assert "extra_user" in usernames


# ── POST /api/users ───────────────────────────────────────────────────────────

def test_create_user_returns_201_for_admin(admin_client):
    response = admin_client.post(
        "/api/users",
        json={"username": "new_member", "password": "langes_pw_1", "role": "member"},
    )
    assert response.status_code == 201


def test_create_user_returns_403_for_member(member_client):
    response = member_client.post(
        "/api/users",
        json={"username": "new_member", "password": "langes_pw_1", "role": "member"},
    )
    assert response.status_code == 403


def test_create_user_duplicate_returns_409(admin_client):
    admin_client.post(
        "/api/users",
        json={"username": "doppelt", "password": "langes_pw_1", "role": "member"},
    )
    response = admin_client.post(
        "/api/users",
        json={"username": "doppelt", "password": "anderes_pw_1", "role": "member"},
    )
    assert response.status_code == 409


def test_create_user_invalid_role_returns_422(admin_client):
    response = admin_client.post(
        "/api/users",
        json={"username": "new_member", "password": "langes_pw_1", "role": "superuser"},
    )
    assert response.status_code == 422


def test_create_user_short_password_returns_422(admin_client):
    response = admin_client.post(
        "/api/users",
        json={"username": "new_member", "password": "kurz", "role": "member"},
    )
    assert response.status_code == 422


# ── DELETE /api/users/{user_id} ───────────────────────────────────────────────

def test_deactivate_user_returns_204(admin_client):
    with s.SessionLocal() as db:
        user = create_user(db, "to_deactivate", "geheim", role="member")
        db.commit()
        user_id = user.id
    assert admin_client.delete(f"/api/users/{user_id}").status_code == 204


def test_deactivate_own_account_returns_409(admin_client):
    me = admin_client.get("/api/users/me").json()
    assert admin_client.delete(f"/api/users/{me['id']}").status_code == 409


def test_deactivate_unknown_user_returns_404(admin_client):
    assert admin_client.delete(f"/api/users/{uuid.uuid4()}").status_code == 404


def test_deactivate_returns_403_for_member(member_client):
    with s.SessionLocal() as db:
        user = create_user(db, "to_deactivate", "geheim", role="member")
        db.commit()
        user_id = user.id
    assert member_client.delete(f"/api/users/{user_id}").status_code == 403
