import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, String, delete
from sqlalchemy.exc import IntegrityError

from app.database.database import Base, SessionLocal

_TOKEN_TTL_DAYS = 7
_TOKEN_HEX_BYTES = 32


class SessionRecord(Base):
    """Persistent session token with expiry and owning user, stored in the database."""

    __tablename__ = "sessions"

    token = Column(String(64), primary_key=True, nullable=False)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    # Nullable so that rows created before the multi-user migration (which
    # have no associated user) can still be represented in the table.
    # All tokens created after the migration carry a user_id.
    user_id = Column(sa.UUID(as_uuid=True), nullable=True, index=True)


def _hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of *token* for safe database storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    """Return the current UTC time as a naive datetime for DB storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_token(user_id: uuid.UUID | None = None) -> str:
    """
    Generate a cryptographically secure random token, associate it with
    *user_id* (if provided), persist it with an expiry timestamp, and return it.

    Parameters
    ----------
    user_id:
        The UUID of the authenticated user this session belongs to.
        ``None`` is accepted for backward compatibility (legacy sessions
        without an owner); such tokens are treated as unauthenticated by
        ``require_session`` and will be rejected by auth-enforcing endpoints.

    Returns
    -------
    str
        The newly created session token (64 hex characters).
    """
    token = secrets.token_hex(_TOKEN_HEX_BYTES)
    now = _now()
    record = SessionRecord(
        token=_hash_token(token),
        created_at=now,
        expires_at=now + timedelta(days=_TOKEN_TTL_DAYS),
        user_id=user_id,
    )
    with SessionLocal() as db:
        db.add(record)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise
    return token


def validate_token(token: str) -> uuid.UUID | None:
    """
    Check whether *token* is an active, non-expired session token.

    Expired tokens are removed from the database on access (lazy eviction).

    Parameters
    ----------
    token:
        The token to validate.

    Returns
    -------
    uuid.UUID | None
        The ``user_id`` of the session owner if the token is valid and
        unexpired, otherwise ``None``.

    Notes
    -----
    The return type is intentionally ``UUID | None`` rather than ``bool``
    so callers can obtain the user identity without a second DB round-trip.
    The falsy contract is preserved: ``None`` is falsy, a UUID is truthy, so
    existing ``if not validate_token(token)`` guards continue to work.
    """
    with SessionLocal() as db:
        record = db.get(SessionRecord, _hash_token(token))
        if record is None:
            return None
        if record.expires_at < _now():
            db.delete(record)
            db.commit()
            return None
        return record.user_id


def revoke_token(token: str) -> None:
    """
    Delete *token* from the database, invalidating the session immediately.

    Parameters
    ----------
    token:
        The token to revoke. No-op if the token is not present.
    """
    with SessionLocal() as db:
        record = db.get(SessionRecord, _hash_token(token))
        if record is not None:
            db.delete(record)
            db.commit()


def purge_expired() -> int:
    """
    Delete all expired session records from the database.

    Intended to be called on application startup to avoid unbounded table
    growth. Lazy eviction in :func:`validate_token` handles the common case;
    this function cleans up tokens that were never validated after expiry.

    Returns
    -------
    int
        The number of rows deleted.
    """
    with SessionLocal() as db:
        result = db.execute(
            delete(SessionRecord).where(SessionRecord.expires_at < _now())
        )
        db.commit()
        return result.rowcount
