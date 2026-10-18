import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest

import app.session.session as s
from app.session.session import SessionRecord
from app.users.model import User

# Fixed user ID used across all session tests. sessions.user_id references
# users.id, so the account is created as a row before every test.
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000deadbeef")


@pytest.fixture(autouse=True)
def _session_owner():
    """Create the account that owns the tokens issued in these tests."""
    with s.SessionLocal() as db:
        db.add(User(id=TEST_USER_ID, username="session-owner", password_hash="x"))
        db.commit()


# ─── Token creation ──────────────────────────────────────────────────────────

def test_create_token_returns_string():
    token = s.create_token(TEST_USER_ID)
    assert isinstance(token, str)


def test_created_token_is_valid():
    token = s.create_token(TEST_USER_ID)
    assert s.validate_token(token) is not None


def test_created_token_returns_correct_user_id():
    token = s.create_token(TEST_USER_ID)
    assert s.validate_token(token) == TEST_USER_ID


# ─── Validation ──────────────────────────────────────────────────────────────

def test_unknown_token_is_invalid():
    assert s.validate_token("not-present") is None


def test_expired_token_is_invalid():
    token = s.create_token(TEST_USER_ID)
    future = s._now() + timedelta(days=s._TOKEN_TTL_DAYS + 1)
    with patch.object(s, "_now", return_value=future):
        assert s.validate_token(token) is None


def test_expired_token_is_removed_from_db():
    token = s.create_token(TEST_USER_ID)
    future = s._now() + timedelta(days=s._TOKEN_TTL_DAYS + 1)
    with patch.object(s, "_now", return_value=future):
        s.validate_token(token)
    # After lazy eviction the record must be gone.
    assert s.validate_token(token) is None


def test_validate_token_uses_the_session_it_is_given():
    """
    Request handlers pass their own session, so authentication does not
    check out a second connection. The lookup must run in that session.
    """
    token = s.create_token(TEST_USER_ID)
    with s.SessionLocal() as db:
        assert s.validate_token(token, db) == TEST_USER_ID
        assert db.get(SessionRecord, s._hash_token(token)) is not None


def test_validate_token_evicts_an_expired_token_in_the_given_session():
    token = s.create_token(TEST_USER_ID)
    future = s._now() + timedelta(days=s._TOKEN_TTL_DAYS + 1)
    with s.SessionLocal() as db, patch.object(s, "_now", return_value=future):
        assert s.validate_token(token, db) is None
    with s.SessionLocal() as db:
        assert db.get(SessionRecord, s._hash_token(token)) is None


def test_token_without_owner_is_accepted_by_the_table():
    """Legacy tokens without an owner remain representable."""
    token = s.create_token()
    assert s.validate_token(token) is None


def test_deleting_the_account_removes_its_sessions():
    token = s.create_token(TEST_USER_ID)
    with s.SessionLocal() as db:
        db.delete(db.get(User, TEST_USER_ID))
        db.commit()
    with s.SessionLocal() as db:
        assert db.get(SessionRecord, s._hash_token(token)) is None


# ─── Revocation ──────────────────────────────────────────────────────────────

def test_revoke_invalidates_token():
    token = s.create_token(TEST_USER_ID)
    s.revoke_token(token)
    assert s.validate_token(token) is None


def test_revoke_unknown_token_is_noop():
    s.revoke_token("not-present")


def test_multiple_tokens_are_independent():
    t1 = s.create_token(TEST_USER_ID)
    t2 = s.create_token(TEST_USER_ID)
    s.revoke_token(t1)
    assert s.validate_token(t1) is None
    assert s.validate_token(t2) is not None


# ─── Uniqueness ──────────────────────────────────────────────────────────────

def test_tokens_are_unique():
    tokens = [s.create_token(TEST_USER_ID) for _ in range(100)]
    assert len(set(tokens)) == 100


# ─── Purge ───────────────────────────────────────────────────────────────────

def test_purge_expired_removes_only_expired_tokens():
    active = s.create_token(TEST_USER_ID)
    expired = s.create_token(TEST_USER_ID)

    future = s._now() + timedelta(days=s._TOKEN_TTL_DAYS + 1)
    with patch.object(s, "_now", return_value=future):
        count = s.purge_expired()

    assert count == 2  # both are expired from the perspective of "future"
    assert s.validate_token(active) is None
    assert s.validate_token(expired) is None


def test_purge_expired_does_not_remove_valid_tokens():
    token = s.create_token(TEST_USER_ID)
    count = s.purge_expired()
    assert count == 0
    assert s.validate_token(token) is not None
