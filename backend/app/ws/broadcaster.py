"""
Event broadcaster.

Serialises a block mutation event into the wire envelope and forwards it to
all active WebSocket connections via the connection manager.

This is the single call-site for WebSocket push. The router layer (Step 2)
will call ``broadcast_block_event`` after each committed mutation; the
service layer remains synchronous and DB-only.

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
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def broadcast_block_event(
    *,
    event_type: str,
    block_id: Optional[str],
    before: Optional[Any],
    after: Optional[Any],
    event_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> None:
    """
    Broadcast a block mutation event to every connected WebSocket client.

    This coroutine is a no-op when no clients are connected, so it is safe
    to call unconditionally after every commit.

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

    payload = {
        "event_id": event_id,
        "event_type": event_type,
        "block_id": block_id,
        "before": before,
        "after": after,
        "created_at": created_at or _iso_now(),
    }
    message = json.dumps({"type": "block.event", "payload": payload})
    await manager.broadcast(message)
