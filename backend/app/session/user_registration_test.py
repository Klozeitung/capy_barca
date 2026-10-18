import threading

import bcrypt
import pytest

import app.session.session as s
import app.session.user_registration as ur
from app.users.model import User
from app.users.repository import create_user, get_by_username


# ─── create_admin ─────────────────────────────────────────────────────────────

def test_create_admin_returns_user():
    user = ur.create_admin("capybarca", "secret")
    assert user.username == "capybarca"


def test_create_admin_sets_admin_role():
    user = ur.create_admin("capybarca", "secret")
    assert user.role == "admin"


def test_create_admin_sets_active():
    user = ur.create_admin("capybarca", "secret")
    assert user.is_active is True


def test_create_admin_hashes_password():
    user = ur.create_admin("capybarca", "secret")
    assert bcrypt.checkpw(b"secret", user.password_hash.encode())


def test_create_admin_persists_to_db():
    ur.create_admin("capybarca", "secret")
    with s.SessionLocal() as db:
        user = get_by_username(db, "capybarca")
    assert user is not None
    assert user.username == "capybarca"


def test_create_admin_duplicate_username_raises():
    ur.create_admin("capybarca", "secret")
    with pytest.raises(Exception):
        ur.create_admin("capybarca", "other")


# ─── One initial admin only ───────────────────────────────────────────────────


def test_create_admin_refuses_once_any_account_exists():
    with s.SessionLocal() as db:
        create_user(db, "person-a", "secret", role="member")
        db.commit()
    with pytest.raises(ur.AlreadyConfigured):
        ur.create_admin("person-b", "secret")
    with s.SessionLocal() as db:
        assert get_by_username(db, "person-b") is None


def test_second_create_admin_is_refused_whatever_the_name():
    ur.create_admin("person-a", "secret")
    with pytest.raises(ur.AlreadyConfigured):
        ur.create_admin("person-b", "secret")


def test_concurrent_create_admin_creates_exactly_one_account():
    """
    Several requests released at the same moment: without the lock more than
    one could pass the existence check before any of them has committed.
    """
    attempts = 8
    barrier = threading.Barrier(attempts)
    created: list[str] = []
    refused: list[str] = []
    failed: list[BaseException] = []

    def _attempt(index: int) -> None:
        barrier.wait()
        try:
            created.append(ur.create_admin(f"person-{index}", "secret").username)
        except ur.AlreadyConfigured:
            refused.append(f"person-{index}")
        except BaseException as exc:  # surfaced below, a thread cannot fail the test
            failed.append(exc)

    threads = [threading.Thread(target=_attempt, args=(i,)) for i in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert failed == []
    assert len(created) == 1
    assert len(refused) == attempts - 1
    with s.SessionLocal() as db:
        assert db.query(User).count() == 1


def test_create_admin_takes_the_registration_lock(monkeypatch):
    from sqlalchemy.orm import Session

    executed: list[tuple[str, dict]] = []
    original_execute = Session.execute

    def _recording(self, statement, params=None, *args, **kwargs):
        executed.append((str(statement), params))
        return original_execute(self, statement, params, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", _recording)
    ur.create_admin("capybarca", "secret")

    assert (
        "SELECT pg_advisory_xact_lock(:key)",
        {"key": ur.REGISTRATION_LOCK_KEY},
    ) in executed


def test_registration_lock_key_differs_from_the_migration_lock():
    from app.database.migration_lock import MIGRATION_LOCK_KEY

    assert ur.REGISTRATION_LOCK_KEY != MIGRATION_LOCK_KEY
