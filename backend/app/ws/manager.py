"""
WebSocket connection manager.

Maintains the set of currently active WebSocket connections and provides a
single broadcast point for server-initiated messages.

The application is designed for a single authenticated user; the manager is
nevertheless implemented as a proper class that handles any number of
concurrent connections, so extending to multi-user requires no structural
changes.
"""
import logging
from typing import Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Registry of active WebSocket connections with broadcast support."""

    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept the WebSocket handshake and register the connection."""
        await websocket.accept()
        self._connections.add(websocket)
        logger.debug(
            "WS connected: %s (total: %d)", id(websocket), len(self._connections)
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket from the active set. No-op if not registered."""
        self._connections.discard(websocket)
        logger.debug(
            "WS disconnected: %s (total: %d)", id(websocket), len(self._connections)
        )

    @property
    def active_count(self) -> int:
        """Number of currently active connections."""
        return len(self._connections)

    async def broadcast(self, message: str) -> None:
        """
        Send *message* to every active connection.

        Connections that raise during send are silently pruned to avoid
        blocking the broadcast loop on stale sockets.
        """
        stale: Set[WebSocket] = set()
        for ws in list(self._connections):
            try:
                await ws.send_text(message)
            except Exception:
                logger.debug("Stale WS removed during broadcast: %s", id(ws))
                stale.add(ws)
        for ws in stale:
            self._connections.discard(ws)


# Module-level singleton shared by the router and the broadcaster.
manager = ConnectionManager()
