"""
Tests for the user management router (GET/POST /api/users, DELETE, PATCH).

All tests run against the PostgreSQL test database provided by the autouse
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
        user = create_user(db, username, "secret", role=role)
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


# ── /api/users/me/recent-icons ───────────────────────────────────────────────


def _use_icon(client: TestClient, icon: str):
    return client.post("/api/users/me/recent-icons", json={"icon": icon})


def _icon_names(response) -> list[str]:
    return [entry["icon"] for entry in response.json()]


def test_recent_icons_start_empty(admin_client):
    response = admin_client.get("/api/users/me/recent-icons")
    assert response.status_code == 200
    assert response.json() == []


def test_using_an_icon_records_it_with_a_timestamp(admin_client):
    response = _use_icon(admin_client, "mdi:star-outline")

    assert response.status_code == 200
    [entry] = response.json()
    assert entry["icon"] == "mdi:star-outline"
    assert entry["used_at"]
    assert admin_client.get("/api/users/me/recent-icons").json() == response.json()


def test_recent_icons_are_newest_first(admin_client):
    for icon in ("mdi:alpha-a", "tabler:alpha-b", "lucide:alpha-c"):
        _use_icon(admin_client, icon)

    response = admin_client.get("/api/users/me/recent-icons")

    assert _icon_names(response) == ["lucide:alpha-c", "tabler:alpha-b", "mdi:alpha-a"]


def test_using_a_listed_icon_again_moves_it_to_the_front_without_a_duplicate(admin_client):
    for icon in ("mdi:alpha-a", "mdi:alpha-b", "mdi:alpha-c"):
        _use_icon(admin_client, icon)
    before = {e["icon"]: e["used_at"] for e in admin_client.get("/api/users/me/recent-icons").json()}

    response = _use_icon(admin_client, "mdi:alpha-a")

    assert _icon_names(response) == ["mdi:alpha-a", "mdi:alpha-c", "mdi:alpha-b"]
    assert response.json()[0]["used_at"] >= before["mdi:alpha-a"]


def test_a_new_icon_replaces_the_one_used_longest_ago_when_the_list_is_full(admin_client):
    from app.users.repository import RECENT_ICONS_LIMIT

    icons = [f"mdi:numeric-{n}" for n in range(RECENT_ICONS_LIMIT)]
    for icon in icons:
        _use_icon(admin_client, icon)

    response = _use_icon(admin_client, "mdi:numeric-x")

    names = _icon_names(response)
    assert len(names) == RECENT_ICONS_LIMIT
    assert names[0] == "mdi:numeric-x"
    assert icons[0] not in names
    assert set(icons[1:]) <= set(names)


def test_reusing_an_icon_of_a_full_list_drops_nothing(admin_client):
    from app.users.repository import RECENT_ICONS_LIMIT

    icons = [f"mdi:numeric-{n}" for n in range(RECENT_ICONS_LIMIT)]
    for icon in icons:
        _use_icon(admin_client, icon)

    response = _use_icon(admin_client, icons[0])

    names = _icon_names(response)
    assert names[0] == icons[0]
    assert sorted(names) == sorted(icons)


def test_record_icon_use_updates_the_timestamp_of_a_listed_icon():
    from datetime import datetime, timezone

    from app.users.repository import record_icon_use

    first = datetime(2000, 7, 28, tzinfo=timezone.utc)
    later = datetime(2045, 7, 17, tzinfo=timezone.utc)
    with s.SessionLocal() as db:
        user = create_user(db, "icon_user", "secret")
        record_icon_use(db, user, "mdi:alpha-a", first)
        record_icon_use(db, user, "mdi:alpha-b", first)
        icons = record_icon_use(db, user, "mdi:alpha-a", later)
        db.commit()

    assert icons == [
        {"icon": "mdi:alpha-a", "used_at": later.isoformat()},
        {"icon": "mdi:alpha-b", "used_at": first.isoformat()},
    ]


@pytest.mark.parametrize(
    "icon",
    ["", "home", "fa:home", "mdi:Home", "mdi:", "mdi:../etc", "mdi:a b", "mdi:-a", "mdi:" + "a" * 130],
)
def test_using_something_that_is_not_an_icon_returns_422(admin_client, icon):
    assert _use_icon(admin_client, icon).status_code == 422
    assert admin_client.get("/api/users/me/recent-icons").json() == []


def test_recent_icons_are_personal(admin_client, member_client):
    _use_icon(admin_client, "mdi:alpha-a")
    assert member_client.get("/api/users/me/recent-icons").json() == []


def test_recent_icons_are_not_part_of_the_user_profile(admin_client):
    _use_icon(admin_client, "mdi:alpha-a")
    assert "recent_icons" not in admin_client.get("/api/users/me").json()
    assert all("recent_icons" not in u for u in admin_client.get("/api/users").json())


def test_recent_icons_return_401_unauthenticated():
    assert anon_client.get("/api/users/me/recent-icons").status_code == 401
    assert _use_icon(anon_client, "mdi:alpha-a").status_code == 401


# ── PATCH /api/users/me/password ─────────────────────────────────────────────

def test_change_password_returns_204(admin_client):
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "secret", "new_password": "new_pw_123"},
    )
    assert response.status_code == 204


def test_change_password_wrong_current_returns_401(admin_client):
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "wrong", "new_password": "new_pw_123"},
    )
    assert response.status_code == 401


def test_change_password_too_short_returns_422(admin_client):
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "secret", "new_password": "short"},
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
        json={"current_password": "secret", "new_password": password},
    )
    assert response.status_code == 422


def test_change_password_rejects_an_over_long_current_password(admin_client):
    """The field that is only ever verified needs the same bound."""
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "a" * 73, "new_password": "new_pw_123"},
    )
    assert response.status_code == 422


def test_change_password_accepts_a_new_password_at_the_byte_ceiling(admin_client):
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "secret", "new_password": "a" * 72},
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
        json={"current_password": "secret", "new_password": "new_pw_123"},
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
        user = create_user(db, "reset_target", "secret", role="member")
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
            json={"current_password": "wrong", "new_password": "new_pw_123"},
        )
    response = admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "wrong", "new_password": "new_pw_123"},
    )
    assert response.status_code == 429


# ── WOPI tokens end with a password change ───────────────────────────────────


def _wopi_token_for(username: str) -> uuid.UUID:
    """Register a WOPI token for *username* and return its id."""
    from datetime import datetime, timedelta, timezone

    from app.blocks.models import Block
    from app.users.model import User
    from app.wopi import registry

    jti = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with s.SessionLocal() as db:
        user = db.query(User).filter(User.username == username).one()
        block = Block(id=uuid.uuid4(), type="page", position=1.0, state="active")
        db.add(block)
        db.flush()
        registry.register(
            db, jti=jti, user_id=user.id, block_id=block.id, file_uuid=uuid.uuid4(),
            issued_at=now, expires_at=now + timedelta(hours=1),
        )
        db.commit()
    return jti


def _wopi_token_exists(jti: uuid.UUID) -> bool:
    from app.wopi.model import WopiToken

    with s.SessionLocal() as db:
        return db.get(WopiToken, jti) is not None


def test_changing_the_own_password_withdraws_the_wopi_tokens(admin_client):
    jti = _wopi_token_for("admin_user")
    admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "secret", "new_password": "new_pw_123"},
    )
    assert not _wopi_token_exists(jti)


def test_a_refused_password_change_keeps_the_wopi_tokens(admin_client):
    jti = _wopi_token_for("admin_user")
    admin_client.patch(
        "/api/users/me/password",
        json={"current_password": "wrong", "new_password": "new_pw_123"},
    )
    assert _wopi_token_exists(jti)


def test_admin_reset_withdraws_the_wopi_tokens_of_that_account_only(admin_client):
    with s.SessionLocal() as db:
        target = create_user(db, "reset_target", "secret", role="member")
        db.commit()
        target_id = target.id
    target_token = _wopi_token_for("reset_target")
    admin_token = _wopi_token_for("admin_user")

    response = admin_client.patch(
        f"/api/users/{target_id}/password", json={"new_password": "new_pw_123"}
    )

    assert response.status_code == 204
    assert not _wopi_token_exists(target_token)
    assert _wopi_token_exists(admin_token)


# ── GET /api/users ────────────────────────────────────────────────────────────

def test_list_users_returns_200_for_admin(admin_client):
    assert admin_client.get("/api/users").status_code == 200


def test_list_users_returns_403_for_member(member_client):
    assert member_client.get("/api/users").status_code == 403


def test_list_users_returns_401_unauthenticated():
    assert anon_client.get("/api/users").status_code == 401


def test_list_users_contains_created_users(admin_client):
    with s.SessionLocal() as db:
        create_user(db, "extra_user", "secret", role="member")
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
        json={"username": "new_member", "password": "short", "role": "member"},
    )
    assert response.status_code == 422


# ── DELETE /api/users/{user_id} ───────────────────────────────────────────────

def test_deactivate_user_returns_204(admin_client):
    with s.SessionLocal() as db:
        user = create_user(db, "to_deactivate", "secret", role="member")
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
        user = create_user(db, "to_deactivate", "secret", role="member")
        db.commit()
        user_id = user.id
    assert member_client.delete(f"/api/users/{user_id}").status_code == 403
