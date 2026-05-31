import bcrypt
import pytest

import app.session.session as s
import app.session.user_registration as ur
from app.users.repository import get_by_username


# ─── create_admin ─────────────────────────────────────────────────────────────

def test_create_admin_returns_user():
    user = ur.create_admin("capybarca", "geheim")
    assert user.username == "capybarca"


def test_create_admin_sets_admin_role():
    user = ur.create_admin("capybarca", "geheim")
    assert user.role == "admin"


def test_create_admin_sets_active():
    user = ur.create_admin("capybarca", "geheim")
    assert user.is_active is True


def test_create_admin_hashes_password():
    user = ur.create_admin("capybarca", "geheim")
    assert bcrypt.checkpw(b"geheim", user.password_hash.encode())


def test_create_admin_persists_to_db():
    ur.create_admin("capybarca", "geheim")
    with s.SessionLocal() as db:
        user = get_by_username(db, "capybarca")
    assert user is not None
    assert user.username == "capybarca"


def test_create_admin_duplicate_username_raises():
    ur.create_admin("capybarca", "geheim")
    with pytest.raises(Exception):
        ur.create_admin("capybarca", "other")
