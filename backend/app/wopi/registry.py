"""
Registry of issued WOPI tokens (see :mod:`app.wopi.model`).

The token router registers every token it issues and asks
:func:`is_registered` on every file request. Password changes call
:func:`revoke_for_user`; logging out needs no call, since the database removes
the tokens of a session together with it. The application startup calls
:func:`purge_expired_tokens`.

All functions except :func:`purge_expired_tokens` work in the caller's session
and leave the commit to the caller, so a revocation lands in the same
transaction as the change that causes it.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.wopi.model import WopiToken


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register(
    db: Session,
    *,
    jti: uuid.UUID,
    user_id: uuid.UUID,
    block_id: uuid.UUID,
    file_uuid: uuid.UUID,
    issued_at: datetime,
    expires_at: datetime,
    session_key: Optional[str] = None,
) -> None:
    """
    Record an issued token, tied to the session with *session_key* when given.
    Expired rows are removed in the same step.
    """
    purge_expired(db)
    db.add(WopiToken(
        jti=jti,
        user_id=user_id,
        block_id=block_id,
        file_uuid=file_uuid,
        session_key=session_key,
        issued_at=issued_at,
        expires_at=expires_at,
    ))
    db.flush()


def is_registered(
    db: Session,
    *,
    jti: uuid.UUID,
    user_id: uuid.UUID,
    block_id: uuid.UUID,
    file_uuid: uuid.UUID,
) -> bool:
    """
    Return True if *jti* names a live token issued for exactly this account,
    block and file.

    Comparing the other claims as well means a registered id cannot be carried
    over into claims naming something else, even by someone who can sign.
    """
    row = db.get(WopiToken, jti)
    return (
        row is not None
        and row.user_id == user_id
        and row.block_id == block_id
        and row.file_uuid == file_uuid
        and row.expires_at > _now()
    )


def revoke_for_user(db: Session, user_id: uuid.UUID) -> int:
    """Withdraw every token of *user_id* and return how many there were."""
    result = db.execute(delete(WopiToken).where(WopiToken.user_id == user_id))
    return result.rowcount


def purge_expired(db: Session) -> int:
    """Delete the expired tokens and return how many there were."""
    result = db.execute(delete(WopiToken).where(WopiToken.expires_at <= _now()))
    return result.rowcount


def purge_expired_tokens() -> int:
    """
    Delete the expired tokens in a session of its own and commit.

    Called at startup, so tokens that expired while the application was not
    issuing new ones do not linger.
    """
    # Read at call time: the test suite substitutes SessionLocal on the module.
    import app.database.database as db_module

    with db_module.SessionLocal() as db:
        removed = purge_expired(db)
        db.commit()
        return removed
