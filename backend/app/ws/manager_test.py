"""
Unit tests for the WebSocket connection manager.

Tests are written as synchronous functions that drive async coroutines via
asyncio.run(), so no pytest-asyncio plugin or special markers are needed.
"""
import asyncio

from app.ws.manager import ConnectionManager


# ─── Stub ─────────────────────────────────────────────────────────────────────


class MockWebSocket:
    """Minimal WebSocket stub for testing the ConnectionManager."""

    def __init__(self) -> None:
        self.accepted: bool = False
        self.sent: list[str] = []
        self._raise_on_send: Exception | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        if self._raise_on_send is not None:
            raise self._raise_on_send
        self.sent.append(message)

    def fail_next_send(self, exc: Exception) -> None:
        """Configure this stub to raise *exc* on the next send_text call."""
        self._raise_on_send = exc


# ─── connect / disconnect ─────────────────────────────────────────────────────


def test_connect_accepts_websocket():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws)
        assert ws.accepted is True
    asyncio.run(_run())


def test_connect_increments_active_count():
    async def _run():
        mgr = ConnectionManager()
        assert mgr.active_count == 0
        await mgr.connect(MockWebSocket())
        assert mgr.active_count == 1
    asyncio.run(_run())


def test_connect_multiple_increments_correctly():
    async def _run():
        mgr = ConnectionManager()
        await mgr.connect(MockWebSocket())
        await mgr.connect(MockWebSocket())
        assert mgr.active_count == 2
    asyncio.run(_run())


def test_disconnect_decrements_active_count():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws)
        mgr.disconnect(ws)
        assert mgr.active_count == 0
    asyncio.run(_run())


def test_disconnect_unknown_is_noop():
    mgr = ConnectionManager()
    mgr.disconnect(MockWebSocket())  # must not raise


def test_disconnect_only_removes_target():
    async def _run():
        mgr = ConnectionManager()
        ws1, ws2 = MockWebSocket(), MockWebSocket()
        await mgr.connect(ws1)
        await mgr.connect(ws2)
        mgr.disconnect(ws1)
        assert mgr.active_count == 1
    asyncio.run(_run())


# ─── broadcast ────────────────────────────────────────────────────────────────


def test_broadcast_delivers_to_single_connection():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws)
        await mgr.broadcast("hello")
        assert ws.sent == ["hello"]
    asyncio.run(_run())


def test_broadcast_delivers_to_all_connections():
    async def _run():
        mgr = ConnectionManager()
        ws1, ws2 = MockWebSocket(), MockWebSocket()
        await mgr.connect(ws1)
        await mgr.connect(ws2)
        await mgr.broadcast("hello")
        assert ws1.sent == ["hello"]
        assert ws2.sent == ["hello"]
    asyncio.run(_run())


def test_broadcast_with_no_connections_is_noop():
    async def _run():
        mgr = ConnectionManager()
        await mgr.broadcast("hello")  # must not raise
    asyncio.run(_run())


def test_broadcast_removes_stale_connection():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        ws.fail_next_send(RuntimeError("connection lost"))
        await mgr.connect(ws)
        await mgr.broadcast("hello")
        assert mgr.active_count == 0
    asyncio.run(_run())


def test_broadcast_continues_past_stale_connection():
    """A failing connection must not prevent delivery to healthy ones."""
    async def _run():
        mgr = ConnectionManager()
        ws_good = MockWebSocket()
        ws_bad = MockWebSocket()
        ws_bad.fail_next_send(RuntimeError("connection lost"))
        await mgr.connect(ws_good)
        await mgr.connect(ws_bad)
        await mgr.broadcast("hello")
        assert ws_good.sent == ["hello"]
        assert mgr.active_count == 1
    asyncio.run(_run())


def test_broadcast_multiple_messages_in_order():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws)
        await mgr.broadcast("first")
        await mgr.broadcast("second")
        assert ws.sent == ["first", "second"]
    asyncio.run(_run())
