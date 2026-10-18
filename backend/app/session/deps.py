"""
Shared FastAPI dependencies for authentication and database access.

All routers import ``require_session``, ``get_current_user``,
``require_admin``, and ``get_db`` from here so auth logic lives in exactly
one place. ``require_block_access`` extends that to object-level
authorization and is called from inside handlers rather than as a
dependency, because the block identifier is not always a path parameter.
"""
import asyncio
import uuid
import weakref
from typing import AsyncIterator, Optional

import anyio
import anyio.to_thread
from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from app.blocks import entry_changes
from app.database.database import POOL_CAPACITY, SessionLocal
from app.session.session import validate_token
from app.users import repository as user_repo
from app.users.model import User


# ─── Database ─────────────────────────────────────────────────────────────────
#
# A request takes its connection in its first dependency and keeps it while it
# moves through the others and the handler, each of which runs as a separate
# job in the worker thread pool. Between those jobs it holds a connection but
# no thread. When many requests arrive at once (the browser sending hundreds
# of saves in one go), the threads fill up with requests asking the pool for a
# connection, while every connection belongs to a request that waits for a
# thread to continue. Nothing moves until the pool's 30-second timeout fails
# the waiting requests.
#
# ``get_db`` therefore admits only as many requests at a time as the pool can
# serve, and the others wait on the event loop, where waiting costs neither a
# thread nor a connection. A few connections stay outside the gate for code
# that opens its own session (the WebSocket handshake, the broadcaster), and
# the thread pool is sized above the gate (see ``configure_worker_threads``),
# so an admitted request always finds a thread for its next step.

# Connections left to sessions opened outside a request.
RESERVED_CONNECTIONS = 5

# Requests that may hold a session at the same time.
REQUEST_SLOTS = max(1, POOL_CAPACITY - RESERVED_CONNECTIONS)

# Worker threads beyond the admitted requests, for everything else that runs
# in the thread pool.
THREAD_HEADROOM = 40

# One gate per event loop. The application runs on one loop; the test client
# starts its own, and a gate is only ever used on the loop it serves. A
# semaphore rather than a capacity limiter, because a slot is not tied to the
# task that took it: the framework may release it from another task.
_gates: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, anyio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def _request_gate() -> anyio.Semaphore:
    """Return the admission gate of the running event loop."""
    loop = asyncio.get_running_loop()
    gate = _gates.get(loop)
    if gate is None:
        gate = anyio.Semaphore(REQUEST_SLOTS)
        _gates[loop] = gate
    return gate


def configure_worker_threads() -> int:
    """
    Size the worker thread pool above the request gate and return its size.

    Called once at start-up. Never lowers a limit that is already higher.
    """
    limiter = anyio.to_thread.current_default_thread_limiter()
    wanted = REQUEST_SLOTS + THREAD_HEADROOM
    if limiter.total_tokens < wanted:
        limiter.total_tokens = wanted
    return int(limiter.total_tokens)


async def get_db() -> AsyncIterator[Session]:
    """
    Yield a database session and close it after the request.

    Waits for one of the ``REQUEST_SLOTS`` first, on the event loop. The close
    runs in a thread of its own outside the shared pool, so returning the
    connection never waits for a thread either.
    """
    async with _request_gate():
        db = SessionLocal()
        try:
            yield db
        finally:
            await anyio.to_thread.run_sync(db.close, limiter=anyio.CapacityLimiter(1))


# ─── Auth ─────────────────────────────────────────────────────────────────────


def require_session(
    session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> uuid.UUID:
    """
    Dependency that enforces a valid session cookie.

    Returns the ``user_id`` embedded in the session token so callers can
    look up the user without an additional cookie read.

    The token lookup runs in the request's own database session. FastAPI
    resolves ``get_db`` once per request, so authentication, the user lookup
    and the handler all share one session and one pooled connection.

    Raises
    ------
    HTTPException(401)
        If the cookie is absent or the token is invalid / expired.
    """
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = validate_token(session, db)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id


def get_current_user(
    user_id: uuid.UUID = Depends(require_session),
    db: Session = Depends(get_db),
) -> User:
    """
    Dependency that resolves the session cookie to a full User object.

    The user is also remembered on the request's database session, so the
    change events the request sends can say who made the change.

    Raises
    ------
    HTTPException(401)
        If the session is invalid or the user account no longer exists /
        has been deactivated.
    """
    user = user_repo.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    entry_changes.set_actor(db, user)
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
