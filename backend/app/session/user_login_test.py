import pytest

import app.session.session as s
import app.session.user_login as ul
from app.users.repository import create_user


@pytest.fixture
def test_user():
    """Insert a test user into the isolated in-memory DB."""
    with s.SessionLocal() as db:
        user = create_user(db, "capybarca", "geheim", role="admin")
        db.commit()
        db.refresh(user)
    return user


# ─── verifyLogin ─────────────────────────────────────────────────────────────

def test_verify_login_correct_credentials(test_user):
    result = ul.verifyLogin("capybarca", "geheim")
    assert result is not None
    assert result.username == "capybarca"


def test_verify_login_wrong_password(test_user):
    assert ul.verifyLogin("capybarca", "falsch") is None


def test_verify_login_wrong_username(test_user):
    assert ul.verifyLogin("anders", "geheim") is None


def test_verify_login_no_users():
    """Returns None when no users exist at all."""
    assert ul.verifyLogin("capybarca", "geheim") is None


def test_verify_login_inactive_user():
    with s.SessionLocal() as db:
        user = create_user(db, "capybarca", "geheim", role="admin")
        user.is_active = False
        db.commit()
    assert ul.verifyLogin("capybarca", "geheim") is None


def test_verify_login_returns_user_with_role(test_user):
    result = ul.verifyLogin("capybarca", "geheim")
    assert result is not None
    assert result.role == "admin"
