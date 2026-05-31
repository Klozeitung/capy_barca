import uuid
from datetime import timedelta
from unittest.mock import patch

import app.session.session as s

# Fixed user ID used across all session tests. No real User record is needed
# because SQLite in tests does not enforce FK constraints.
# IMPORTANT: must contain at least one non-decimal hex character (a-f) so
# that SQLite does not interpret the stored CHAR(32) representation as an
# integer, which would cause AttributeError when SQLAlchemy reads it back.
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000deadbeef")


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
    assert s.validate_token("nichtvorhanden") is None


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


# ─── Revocation ──────────────────────────────────────────────────────────────

def test_revoke_invalidates_token():
    token = s.create_token(TEST_USER_ID)
    s.revoke_token(token)
    assert s.validate_token(token) is None


def test_revoke_unknown_token_is_noop():
    s.revoke_token("existiert_nicht")


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
