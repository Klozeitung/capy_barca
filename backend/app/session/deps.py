"""
Shared FastAPI dependencies for authentication and database access.

All routers import ``require_session``, ``get_current_user``,
``require_admin``, and ``get_db`` from here so auth logic lives in exactly
one place. ``require_block_access`` extends that to object-level
authorization and is called from inside handlers rather than as a
dependency, because the block identifier is not always a path parameter.
"""
import uuid
from typing import Optional

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.database import SessionLocal
from app.session.session import validate_token
from app.users import repository as user_repo
from app.users.model import User


# ─── Database ─────────────────────────────────────────────────────────────────


def get_db():
    """Yield a database session and ensure it is closed after the request."""
    with SessionLocal() as db:
        yield db


# ─── Auth ─────────────────────────────────────────────────────────────────────


def require_session(session: Optional[str] = Cookie(default=None)) -> uuid.UUID:
    """
    Dependency that enforces a valid session cookie.

    Returns the ``user_id`` embedded in the session token so callers can
    look up the user without an additional cookie read.

    Raises
    ------
    HTTPException(401)
        If the cookie is absent or the token is invalid / expired.
    """
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = validate_token(session)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id


def get_current_user(
    user_id: uuid.UUID = Depends(require_session),
    db: Session = Depends(get_db),
) -> User:
    """
    Dependency that resolves the session cookie to a full User object.

    Raises
    ------
    HTTPException(401)
        If the session is invalid or the user account no longer exists /
        has been deactivated.
    """
    user = user_repo.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """
    Dependency that restricts access to admin users.

    Raises
    ------
    HTTPException(403)
        If the authenticated user does not have the ``admin`` role.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")
    return current_user


# ─── Object-level authorization ───────────────────────────────────────────────


def require_block_access(
    db: Session,
    block_id: uuid.UUID,
    user: User,
) -> None:
    """
    Enforce that *user* may act on *block_id*.

    A valid session says who the caller is, not what they may touch. Every
    endpoint that addresses a block by id has to ask this question as well,
    otherwise any authenticated account reaches every block by guessing or
    reading an id. The check delegates to the permission layer, which walks
    the parent chain and lets admins through.

    The permission model does not distinguish reading from writing, so this
    single gate covers both. A block with no explicit permission row anywhere
    in its parent chain resolves to ``everyone``, which is the behaviour of
    an unconfigured workspace and is preserved deliberately.

    Raises
    ------
    HTTPException(403)
        If the user may not access the block.
    """
    # Local import: the permission layer reaches into app.blocks.models, which
    # keeps the import graph acyclic only as long as this stays inside the call.
    from app.permissions import repository as perm_repo

    if not perm_repo.can_user_access(db, block_id, user):
        raise HTTPException(status_code=403, detail="Not authorized for this block")
