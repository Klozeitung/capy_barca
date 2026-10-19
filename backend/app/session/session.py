import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, String, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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
    # All tokens created after the migration carry a user_id. Deleting the
    # account removes its sessions (ON DELETE CASCADE, migration 0008).
    user_id = Column(
        sa.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )


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


def validate_token(token: str, db: Session | None = None) -> uuid.UUID | None:
    """
    Check whether *token* is an active, non-expired session token.

    Expired tokens are removed from the database on access (lazy eviction).

    Parameters
    ----------
    token:
        The token to validate.
    db:
        Session to run the lookup in. Request handlers pass the session of
        the request, so authentication costs no extra connection checkout.
        Without one, a short-lived session is opened for the call.

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
    if db is not None:
        return _validate_in(db, token)
    with SessionLocal() as own_db:
        return _validate_in(own_db, token)


def _validate_in(db: Session, token: str) -> uuid.UUID | None:
    """Look *token* up in *db*, evicting it when it has expired."""
    record = db.get(SessionRecord, _hash_token(token))
    if record is None:
        return None
    if record.expires_at < _now():
        db.delete(record)
        db.commit()
        return None
    return record.user_id


def record_key(token: str, db: Session) -> str | None:
    """
    Return the key under which *token* is stored, or None if no session
    with that token exists.

    The key is what other tables reference to tie a row to one session, so
    the row is removed together with the session (logout, expiry, deletion
    of the account).
    """
    key = _hash_token(token)
    return key if db.get(SessionRecord, key) is not None else None


def revoke_token(token: str) -> None:
    """
    Delete *token* from the database, invalidating the session immediately.

    Rows that reference the session by its key go with it (for example the
    WOPI tokens issued in it, see ``app.wopi.model``).

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
