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

Sending
-------
Every connection owns a bounded outbox and a task that sends from it; that
task is the only writer to the socket. :meth:`ConnectionManager.broadcast`
and :meth:`ConnectionManager.send` only place the message in an outbox and
return at once, so the code that produced it never waits for a client to
read it.

A client that stops reading (a frozen background tab, a sleeping device, a
browser busy for many seconds) would otherwise block every sender once the
socket buffers are full. Request handlers send their events while holding a
worker thread and a pooled database connection, so a single such client used
to stall every mutating request and, once all of them were taken, every
request at all.

A client counts as too slow when one send takes longer than
:data:`SEND_TIMEOUT` seconds or when its outbox holds :data:`OUTBOX_LIMIT`
messages. Its connection is then dropped and closed with code 1013 ("try
again later"); the client reconnects on its own. Everyone else is unaffected.
"""
import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Decides whether the account owning a connection receives a given message.
RecipientFilter = Callable[[uuid.UUID], bool]

# Messages a connection may have waiting before it counts as too slow. Large
# enough for the bursts of a bulk operation, small enough to bound memory.
OUTBOX_LIMIT = 1000

# Seconds a single send may take before the connection counts as too slow.
SEND_TIMEOUT = 15.0

# Seconds the close handshake of a dropped connection may take. A client that
# does not read cannot take the close frame either, so this is short.
CLOSE_TIMEOUT = 2.0

# Close code for a dropped slow client: "try again later" (RFC 6455 registry).
CLOSE_TOO_SLOW = 1013


@dataclass
class _Connection:
    """One registered socket: its account, its outbox and its sending task."""

    user_id: uuid.UUID
    outbox: "asyncio.Queue[str]"
    sender: Optional["asyncio.Task[None]"] = None


class ConnectionManager:
    """Registry of active WebSocket connections and their owning accounts."""

    def __init__(self) -> None:
        self._connections: Dict[WebSocket, _Connection] = {}
        # Close tasks of dropped connections, kept so they are not collected
        # before they finish.
        self._closing: Set["asyncio.Task[None]"] = set()

    async def connect(self, websocket: WebSocket, user_id: uuid.UUID) -> None:
        """
        Accept the WebSocket handshake and register the connection.

        *user_id* is the account the session cookie resolved to. It is recorded
        rather than re-derived later, because the cookie is only available
        during the handshake. The connection's sending task starts here.
        """
        await websocket.accept()
        connection = _Connection(user_id=user_id, outbox=asyncio.Queue(maxsize=OUTBOX_LIMIT))
        self._connections[websocket] = connection
        connection.sender = asyncio.create_task(self._send_loop(websocket, connection))
        logger.debug(
            "WS connected: %s for user %s (total: %d)",
            id(websocket), user_id, len(self._connections),
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """
        Remove a WebSocket from the active set and stop its sending task.

        Messages still in its outbox are discarded. No-op if not registered.
        """
        self._drop(websocket, close=False)
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
        return {c.user_id for c in self._connections.values()}

    def user_id_for(self, websocket: WebSocket) -> Optional[uuid.UUID]:
        """Return the account owning *websocket*, or None if it is not registered."""
        connection = self._connections.get(websocket)
        return connection.user_id if connection is not None else None

    def broadcast(
        self,
        message: str,
        *,
        recipient_filter: Optional[RecipientFilter] = None,
    ) -> int:
        """
        Queue *message* for matching connections and return how many took it.

        Never waits for a client: the message goes into each recipient's
        outbox and is sent from there. Must be called on the event loop that
        serves the connections.

        ``recipient_filter`` receives the account id owning each connection and
        decides whether that connection is sent the message. Passing ``None``
        reaches every active connection, which is only ever correct for a
        payload that carries no block content — the caller has to have made
        that judgement, because this class cannot.

        A connection whose outbox is full is dropped as too slow and does not
        count.
        """
        queued = 0
        for websocket, connection in list(self._connections.items()):
            if recipient_filter is not None and not recipient_filter(connection.user_id):
                continue
            try:
                connection.outbox.put_nowait(message)
                queued += 1
            except asyncio.QueueFull:
                logger.warning(
                    "WS %s for user %s dropped: %d messages waiting",
                    id(websocket), connection.user_id, OUTBOX_LIMIT,
                )
                self._drop(websocket)
        return queued

    def send(self, websocket: WebSocket, message: str) -> bool:
        """
        Queue *message* for one connection, behind what it already has waiting.

        Replies such as a pong go through here rather than straight to the
        socket, so only the connection's own sending task ever writes to it.
        Returns False when the connection is not registered or was dropped
        because its outbox is full.
        """
        connection = self._connections.get(websocket)
        if connection is None:
            return False
        try:
            connection.outbox.put_nowait(message)
        except asyncio.QueueFull:
            self._drop(websocket)
            return False
        return True

    async def flush(self) -> None:
        """Wait until every message queued so far has been sent or discarded."""
        for connection in list(self._connections.values()):
            if connection.sender is not None and not connection.sender.done():
                await connection.outbox.join()

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _send_loop(self, websocket: WebSocket, connection: _Connection) -> None:
        """Send the outbox of one connection, in order, until it is dropped."""
        while True:
            message = await connection.outbox.get()
            try:
                await asyncio.wait_for(websocket.send_text(message), SEND_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(
                    "WS %s for user %s dropped: a send took longer than %.0fs",
                    id(websocket), connection.user_id, SEND_TIMEOUT,
                )
                self._drop(websocket, close=True)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Stale WS removed during send: %s", id(websocket))
                self._drop(websocket, close=False)
                return
            finally:
                connection.outbox.task_done()

    def _drop(self, websocket: WebSocket, *, close: bool = True) -> None:
        """
        Unregister *websocket*, discard its outbox and, if asked, close it.

        The close runs as its own task with a time limit, so dropping never
        waits on the client either. Safe to call from the connection's own
        sending task, which then simply ends.
        """
        connection = self._connections.pop(websocket, None)
        if connection is None:
            return
        try:
            current = asyncio.current_task()
        except RuntimeError:
            # Called outside the event loop; it cannot be the sending task.
            current = None
        sender = connection.sender
        if sender is not None and sender is not current:
            sender.cancel()
        while not connection.outbox.empty():
            connection.outbox.get_nowait()
            connection.outbox.task_done()
        if close:
            task = asyncio.get_running_loop().create_task(self._close(websocket))
            self._closing.add(task)
            task.add_done_callback(self._closing.discard)

    @staticmethod
    async def _close(websocket: WebSocket) -> None:
        """Close a dropped connection; give up quietly if the client cannot take it."""
        with contextlib.suppress(Exception):
            await asyncio.wait_for(websocket.close(code=CLOSE_TOO_SLOW), CLOSE_TIMEOUT)


# Module-level singleton shared by the router and the broadcaster.
manager = ConnectionManager()
