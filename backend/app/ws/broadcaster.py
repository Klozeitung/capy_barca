"""
Event broadcaster.

Serialises a block mutation event into the wire envelope and forwards it to the
WebSocket connections that are allowed to see it.

This is the single call-site for WebSocket push, and the single place where the
question "who may see this event?" is answered. Request handlers are
synchronous and run in worker threads; they call ``emit_block_event`` after
each committed mutation. ``broadcast_block_event`` is the counterpart for code
running on the event loop. The service layer remains synchronous and DB-only.

Scoping
-------
Every event carries ``before`` and ``after`` snapshots, and for most event types
those contain the block's full ``content``. Fanning them out unfiltered would
hand every connected client the contents of blocks they are not permitted to
read, which would make the permission filtering on the read endpoints pointless
the moment a second client is online. Each recipient is therefore checked
against the permission layer before the message is sent.

The check runs per send rather than being cached at connect time, so an account
that loses access stops receiving events immediately instead of at its next
reconnect — the same live-check property the WOPI file endpoints have.

Three rules, in order:

* ``purged`` reaches every connection belonging to a live account. After a purge
  the block row and its permission row are both gone, so there is nothing left
  to evaluate a rule against. This payload is the one that carries no content:
  ``before`` and ``after`` are both null and only the id and a timestamp remain.
  Clients need it to drop a block from their view, so it is delivered, and the
  residual disclosure — that some id ceased to exist — is accepted deliberately.
* An event with no ``block_id`` reaches admins only. These are workspace-level
  and rare, and there is no object to check them against.
* Everything else reaches the accounts that ``can_user_access`` admits.

An account that has been deleted or deactivated mid-session receives nothing in
any of the three cases.

Wire format (server → client):
    {
        "type": "block.event",
        "payload": {
            "event_id":   "<uuid | null>",
            "event_type": "<str>",
            "block_id":   "<uuid | null>",
            "before":     <any | null>,
            "after":      <any | null>,
            "created_at": "<ISO-8601>"
        }
    }
"""
import functools
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Optional, Set

import anyio
import anyio.from_thread
import anyio.to_thread

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# The one event type whose block no longer exists by the time it is sent.
PURGE_EVENT_TYPE = "purged"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_block_id(block_id: Optional[str]) -> Optional[uuid.UUID]:
    """Return *block_id* as a UUID, or None if it is absent or unparseable."""
    if block_id is None:
        return None
    try:
        return uuid.UUID(str(block_id))
    except (AttributeError, TypeError, ValueError):
        logger.debug("Broadcast carried an unusable block_id: %r", block_id)
        return None


def _allowed_user_ids(
    user_ids: Iterable[uuid.UUID],
    *,
    event_type: str,
    block_id: Optional[str],
    db: Optional["Session"] = None,
) -> Set[uuid.UUID]:
    """
    Return the subset of *user_ids* permitted to receive this event.

    Any account that cannot be resolved to an active user is dropped: a
    session may outlive the account it was issued to, and a WebSocket held
    open across a deactivation must not keep receiving.

    The cost does not grow with the number of connected accounts: all of them
    are loaded in one query, and the permission of the block is resolved once
    and then applied to each account, since it does not depend on who asks.

    *db* is the session to use. Request handlers pass their own, so the check
    runs in the request's transaction and connection; without one, a
    short-lived session is opened.
    """
    if db is not None:
        return _allowed_in(db, user_ids, event_type=event_type, block_id=block_id)

    # Imported inside the call so the module attribute is read at call time.
    # The test fixture redirects SessionLocal on the module, which a name bound
    # at import time would not see.
    import app.database.database as db_module

    with db_module.SessionLocal() as own_db:
        return _allowed_in(own_db, user_ids, event_type=event_type, block_id=block_id)


def _allowed_in(
    db: "Session",
    user_ids: Iterable[uuid.UUID],
    *,
    event_type: str,
    block_id: Optional[str],
) -> Set[uuid.UUID]:
    """Evaluate the three scoping rules for *user_ids* inside *db*."""
    from sqlalchemy import select

    from app.permissions import repository as perm_repo
    from app.users.model import User

    candidates: Set[uuid.UUID] = set()
    for user_id in user_ids:
        try:
            candidates.add(uuid.UUID(str(user_id)))
        except (AttributeError, TypeError, ValueError):
            # A malformed identifier cannot name an account.
            continue
    if not candidates:
        return set()

    active = [
        user
        for user in db.scalars(select(User).where(User.id.in_(candidates)))
        if user.is_active
    ]
    if not active:
        return set()

    if event_type == PURGE_EVENT_TYPE:
        return {user.id for user in active}

    target = _coerce_block_id(block_id)
    if target is None:
        # Workspace-level event, or a block_id that will not parse.
        return {user.id for user in active if user.role == "admin"}

    if all(user.role == "admin" for user in active):
        # Admins pass every check; resolving the permission would be wasted.
        return {user.id for user in active}

    effective = perm_repo.resolve_effective_permissions(db, [target])[target]
    return {user.id for user in active if effective.admits(user)}


def _envelope(
    *,
    event_type: str,
    block_id: Optional[str],
    before: Optional[Any],
    after: Optional[Any],
    event_id: Optional[str],
    created_at: Optional[str],
) -> str:
    """Serialise one event into the wire format described in the module docstring."""
    payload = {
        "event_id": event_id,
        "event_type": event_type,
        "block_id": block_id,
        "before": before,
        "after": after,
        "created_at": created_at or _iso_now(),
    }
    return json.dumps({"type": "block.event", "payload": payload})


async def broadcast_block_event(
    *,
    event_type: str,
    block_id: Optional[str],
    before: Optional[Any],
    after: Optional[Any],
    event_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> int:
    """
    Broadcast a block mutation event to the clients permitted to see it.

    For callers that run on the event loop. Returns the number of connections
    the event was queued for; the manager sends it from there without anyone
    waiting on the clients. A no-op when nobody is connected, so it stays safe
    to call unconditionally after every commit.

    The recipient check needs the database, which is synchronous; it runs in
    a worker thread so the event loop keeps serving other requests and the
    open WebSocket connections meanwhile. Request handlers, which run in a
    worker thread already, use :func:`emit_block_event` instead.

    Parameters
    ----------
    event_type:
        Semantic label, e.g. ``"content_updated"``, ``"moved"``,
        ``"state_changed"``.
    block_id:
        UUID string of the affected block. ``None`` for workspace-level
        events that have no specific block.
    before:
        Pre-mutation snapshot. ``None`` for creation events.
    after:
        Post-mutation snapshot. ``None`` for hard-deletion events.
    event_id:
        Optional primary key of the persisted ``BlockEvent`` record,
        allowing clients to correlate WS events with the audit log.
    created_at:
        ISO-8601 timestamp string. Defaults to the current UTC instant.
    """
    # Local import to break any potential circular dependency at module load
    # time (manager imports nothing from the rest of the app).
    from app.ws.manager import manager

    if manager.active_count == 0:
        # Nobody is listening; do not open a database session to find that out.
        return 0

    message = _envelope(
        event_type=event_type,
        block_id=block_id,
        before=before,
        after=after,
        event_id=event_id,
        created_at=created_at,
    )

    allowed = await anyio.to_thread.run_sync(
        functools.partial(
            _allowed_user_ids,
            manager.user_ids(),
            event_type=event_type,
            block_id=block_id,
        )
    )
    if not allowed:
        return 0

    # A connection opened between the lookup above and the send below is only
    # reached if its account was already admitted, so the race resolves closed.
    return manager.broadcast(
        message, recipient_filter=lambda user_id: user_id in allowed
    )


def emit_block_event(
    *,
    event_type: str,
    block_id: Optional[str],
    before: Optional[Any],
    after: Optional[Any],
    event_id: Optional[str] = None,
    created_at: Optional[str] = None,
    db: Optional["Session"] = None,
) -> int:
    """
    Broadcast a block mutation event from a synchronous request handler.

    Request handlers run in AnyIO's worker threads so that their blocking
    database work never stalls the event loop. This function performs the
    recipient check in that same thread, using the handler's session when
    *db* is given, and hands the event to the event loop, which queues it for
    each recipient. It returns as soon as the event is queued and never waits
    for a client to read it: the handler holds a worker thread and a database
    connection, and one client that stopped reading must not keep them.

    Parameters are those of :func:`broadcast_block_event`, plus *db*. Returns
    the number of connections the event was queued for.

    Called outside a worker thread there is no event loop to hand the send
    to. The event is then dropped with a warning instead of failing a request
    whose changes are already committed.
    """
    from app.ws.manager import manager

    if manager.active_count == 0:
        return 0

    message = _envelope(
        event_type=event_type,
        block_id=block_id,
        before=before,
        after=after,
        event_id=event_id,
        created_at=created_at,
    )
    allowed = _allowed_user_ids(
        manager.user_ids(), event_type=event_type, block_id=block_id, db=db
    )
    if not allowed:
        return 0

    send = functools.partial(
        manager.broadcast, message, recipient_filter=lambda user_id: user_id in allowed
    )
    try:
        return anyio.from_thread.run_sync(send)
    except RuntimeError:
        logger.warning(
            "Dropped %s event for %s: emit_block_event was called outside a "
            "worker thread, so no event loop was available to send it.",
            event_type, block_id,
        )
        return 0
