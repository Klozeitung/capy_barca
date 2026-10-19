import pytest
from fastapi.testclient import TestClient

import app.session.session as s
from app.main import app
from app.users.repository import create_user

# The session cookie carries the Secure flag whenever DEBUG is not "true",
# which is the production default. A cookie jar never returns a Secure cookie
# over a plain-HTTP connection, so a client on the default http://testserver
# would silently drop the session on every follow-up request and every
# authenticated assertion below would fail with 401.
#
# Addressing the app over HTTPS reflects the only deployment the installer
# produces (Tailscale certificates are mandatory, nginx and uvicorn both
# terminate TLS) and keeps the suite independent of the ambient DEBUG value.
client = TestClient(app, base_url="https://testserver")


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
def test_user():
    """Insert a test user into the isolated DB before each test."""
    with s.SessionLocal() as db:
        create_user(db, "capybarca", "geheim", role="admin")
        db.commit()


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


def test_login_cookie_is_httponly_and_samesite_strict():
    response = client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_attributes(response)
    assert "samesite=strict" in header


def test_login_cookie_has_secure_flag_outside_debug(monkeypatch):
    """Production default: DEBUG unset or false issues a Secure cookie."""
    monkeypatch.delenv("DEBUG", raising=False)
    response = client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    assert "secure" in cookie_attributes(response)


def test_login_cookie_omits_secure_flag_in_debug_mode(monkeypatch):
    """DEBUG=true drops the Secure flag so local HTTP development works."""
    monkeypatch.setenv("DEBUG", "true")
    response = client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    assert "secure" not in cookie_attributes(response)


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


@pytest.mark.parametrize("password", ["a" * 73, "ä" * 40, "🙂" * 20])
def test_login_rejects_a_password_bcrypt_would_refuse(password):
    """
    bcrypt raises on anything past 72 bytes rather than reporting a mismatch,
    so an over-long password turned a failed login into a 500. Not in the
    audit's list for this finding, but the same defect as the one it names.
    """
    response = client.post(
        "/api/login", json={"username": "capybarca", "password": password}
    )
    assert response.status_code == 422


def test_login_still_accepts_a_short_legacy_password():
    """
    No minimum on the login field. An account created before the minimum
    existed has to remain able to sign in, otherwise it can never reach the
    password change either.
    """
    response = client.post(
        "/api/login", json={"username": "capybarca", "password": "geheim"}
    )
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


def test_verify_returns_200_after_login_in_debug_mode(monkeypatch):
    """The session round-trip must work in both cookie modes."""
    monkeypatch.setenv("DEBUG", "true")
    client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    assert client.get("/api/verify").status_code == 200


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


def test_logout_withdraws_the_wopi_tokens_of_its_session_only():
    """
    A token is tied to the session it was issued in. Logging out ends the
    editor sessions opened there and leaves those of another device alone.
    """
    import uuid
    from datetime import datetime, timedelta, timezone

    from app.blocks.models import Block
    from app.users.model import User
    from app.wopi import registry
    from app.wopi.model import WopiToken

    client.post("/api/login", json={"username": "capybarca", "password": "geheim"})
    current = client.cookies.get("session")
    with s.SessionLocal() as db:
        user = db.query(User).filter(User.username == "capybarca").one()
        other = s.create_token(user.id)
        block = Block(id=uuid.uuid4(), type="page", position=1.0, state="active")
        db.add(block)
        db.flush()
        now = datetime.now(timezone.utc)
        ids = {}
        for name, token in (("current", current), ("other", other)):
            ids[name] = uuid.uuid4()
            registry.register(
                db, jti=ids[name], user_id=user.id, block_id=block.id,
                file_uuid=uuid.uuid4(), issued_at=now, expires_at=now + timedelta(hours=1),
                session_key=s.record_key(token, db),
            )
        db.commit()

    client.post("/api/logout")

    with s.SessionLocal() as db:
        assert db.get(WopiToken, ids["current"]) is None
        assert db.get(WopiToken, ids["other"]) is not None
