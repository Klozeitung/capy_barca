"""
Event broadcaster.

Serialises a block mutation event into the wire envelope and forwards it to the
WebSocket connections that are allowed to see it.

This is the single call-site for WebSocket push, and the single place where the
question "who may see this event?" is answered. The router layer calls
``broadcast_block_event`` after each committed mutation; the service layer
remains synchronous and DB-only.

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
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Set

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
) -> Set[uuid.UUID]:
    """
    Return the subset of *user_ids* permitted to receive this event.

    One database session, one lookup per distinct account. Any account that
    cannot be resolved to an active user is dropped: a session may outlive the
    account it was issued to, and a WebSocket held open across a deactivation
    must not keep receiving.
    """
    # Imported inside the call so the module attribute is read at call time.
    # The test fixture redirects SessionLocal on the module, which a name bound
    # at import time would not see.
    import app.database.database as db_module
    from app.permissions import repository as perm_repo
    from app.users import repository as user_repo

    target = _coerce_block_id(block_id)
    allowed: Set[uuid.UUID] = set()

    with db_module.SessionLocal() as db:
        for user_id in user_ids:
            try:
                user = user_repo.get_by_id(db, user_id)
            except Exception:
                # A malformed identifier cannot name an account.
                user = None
            if user is None or not user.is_active:
                continue

            if event_type == PURGE_EVENT_TYPE:
                allowed.add(user_id)
                continue

            if target is None:
                # Workspace-level event, or a block_id that will not parse.
                if user.role == "admin":
                    allowed.add(user_id)
                continue

            if perm_repo.can_user_access(db, target, user):
                allowed.add(user_id)

    return allowed


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

    Returns the number of connections reached. A no-op when nobody is
    connected, so it stays safe to call unconditionally after every commit.

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

    payload = {
        "event_id": event_id,
        "event_type": event_type,
        "block_id": block_id,
        "before": before,
        "after": after,
        "created_at": created_at or _iso_now(),
    }
    message = json.dumps({"type": "block.event", "payload": payload})

    allowed = _allowed_user_ids(
        manager.user_ids(), event_type=event_type, block_id=block_id
    )
    if not allowed:
        return 0

    # A connection opened between the lookup above and the send below is only
    # reached if its account was already admitted, so the race resolves closed.
    return await manager.broadcast(
        message, recipient_filter=lambda user_id: user_id in allowed
    )
