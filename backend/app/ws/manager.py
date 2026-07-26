"""
WebSocket connection manager.

Maintains the set of currently active WebSocket connections together with the
account each one belongs to, and provides a single send point for
server-initiated messages.

The manager deliberately holds no authorization logic. It knows which socket
belongs to which account and nothing beyond that; deciding who may receive a
given message is the broadcaster's job, because that is where the payload and
the block it describes are both in hand. Keeping the two apart means there is
exactly one place to look for the rule and exactly one place to look for the
plumbing.
"""
import logging
import uuid
from typing import Callable, Dict, List, Optional, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Decides whether the account owning a connection receives a given message.
RecipientFilter = Callable[[uuid.UUID], bool]


class ConnectionManager:
    """Registry of active WebSocket connections and their owning accounts."""

    def __init__(self) -> None:
        self._connections: Dict[WebSocket, uuid.UUID] = {}

    async def connect(self, websocket: WebSocket, user_id: uuid.UUID) -> None:
        """
        Accept the WebSocket handshake and register the connection.

        *user_id* is the account the session cookie resolved to. It is recorded
        rather than re-derived later, because the cookie is only available
        during the handshake.
        """
        await websocket.accept()
        self._connections[websocket] = user_id
        logger.debug(
            "WS connected: %s for user %s (total: %d)",
            id(websocket), user_id, len(self._connections),
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket from the active set. No-op if not registered."""
        self._connections.pop(websocket, None)
        logger.debug(
            "WS disconnected: %s (total: %d)", id(websocket), len(self._connections)
        )

    @property
    def active_count(self) -> int:
        """Number of currently active connections."""
        return len(self._connections)

    def user_ids(self) -> Set[uuid.UUID]:
        """
        Distinct accounts holding at least one connection.

        The broadcaster resolves permissions once per account rather than once
        per socket, so a client with several tabs open costs one lookup.
        """
        return set(self._connections.values())

    def user_id_for(self, websocket: WebSocket) -> Optional[uuid.UUID]:
        """Return the account owning *websocket*, or None if it is not registered."""
        return self._connections.get(websocket)

    async def broadcast(
        self,
        message: str,
        *,
        recipient_filter: Optional[RecipientFilter] = None,
    ) -> int:
        """
        Send *message* to matching connections and return how many were reached.

        ``recipient_filter`` receives the account id owning each connection and
        decides whether that connection is sent the message. Passing ``None``
        reaches every active connection, which is only ever correct for a
        payload that carries no block content — the caller has to have made
        that judgement, because this class cannot.

        Connections that raise during send are pruned, so one dead socket never
        stops delivery to the rest.
        """
        delivered = 0
        stale: List[WebSocket] = []
        for websocket, user_id in list(self._connections.items()):
            if recipient_filter is not None and not recipient_filter(user_id):
                continue
            try:
                await websocket.send_text(message)
                delivered += 1
            except Exception:
                logger.debug("Stale WS removed during broadcast: %s", id(websocket))
                stale.append(websocket)
        for websocket in stale:
            self._connections.pop(websocket, None)
        return delivered


# Module-level singleton shared by the router and the broadcaster.
manager = ConnectionManager()
