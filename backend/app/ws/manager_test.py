"""
Unit tests for the WebSocket connection manager.

Tests are written as synchronous functions that drive async coroutines via
asyncio.run(), so no pytest-asyncio plugin or special markers are needed.

The manager holds no authorization logic of its own — it records which socket
belongs to which account and applies whatever filter the broadcaster hands it.
Both halves of that contract are pinned here; the rules themselves are tested
in broadcaster_test.py.
"""
import asyncio
import uuid

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


def _user() -> uuid.UUID:
    return uuid.uuid4()


# ─── connect / disconnect ─────────────────────────────────────────────────────


def test_connect_accepts_websocket():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws, _user())
        assert ws.accepted is True
    asyncio.run(_run())


def test_connect_increments_active_count():
    async def _run():
        mgr = ConnectionManager()
        assert mgr.active_count == 0
        await mgr.connect(MockWebSocket(), _user())
        assert mgr.active_count == 1
    asyncio.run(_run())


def test_connect_multiple_increments_correctly():
    async def _run():
        mgr = ConnectionManager()
        await mgr.connect(MockWebSocket(), _user())
        await mgr.connect(MockWebSocket(), _user())
        assert mgr.active_count == 2
    asyncio.run(_run())


def test_connect_records_the_owning_account():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        user_id = _user()
        await mgr.connect(ws, user_id)
        assert mgr.user_id_for(ws) == user_id
    asyncio.run(_run())


def test_user_id_for_unknown_socket_is_none():
    mgr = ConnectionManager()
    assert mgr.user_id_for(MockWebSocket()) is None


def test_user_ids_returns_distinct_accounts():
    """Several tabs from one account must not cost several permission lookups."""
    async def _run():
        mgr = ConnectionManager()
        user_id = _user()
        await mgr.connect(MockWebSocket(), user_id)
        await mgr.connect(MockWebSocket(), user_id)
        await mgr.connect(MockWebSocket(), _user())
        assert len(mgr.user_ids()) == 2
        assert mgr.active_count == 3
    asyncio.run(_run())


def test_user_ids_is_empty_without_connections():
    assert ConnectionManager().user_ids() == set()


def test_disconnect_decrements_active_count():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws, _user())
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
        await mgr.connect(ws1, _user())
        await mgr.connect(ws2, _user())
        mgr.disconnect(ws1)
        assert mgr.active_count == 1
    asyncio.run(_run())


def test_disconnect_forgets_the_account_mapping():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws, _user())
        mgr.disconnect(ws)
        assert mgr.user_ids() == set()
    asyncio.run(_run())


# ─── broadcast ────────────────────────────────────────────────────────────────


def test_broadcast_delivers_to_single_connection():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws, _user())
        await mgr.broadcast("hello")
        assert ws.sent == ["hello"]
    asyncio.run(_run())


def test_broadcast_delivers_to_all_connections():
    async def _run():
        mgr = ConnectionManager()
        ws1, ws2 = MockWebSocket(), MockWebSocket()
        await mgr.connect(ws1, _user())
        await mgr.connect(ws2, _user())
        await mgr.broadcast("hello")
        assert ws1.sent == ["hello"]
        assert ws2.sent == ["hello"]
    asyncio.run(_run())


def test_broadcast_with_no_connections_is_noop():
    async def _run():
        mgr = ConnectionManager()
        await mgr.broadcast("hello")  # must not raise
    asyncio.run(_run())


def test_broadcast_returns_the_number_delivered():
    async def _run():
        mgr = ConnectionManager()
        await mgr.connect(MockWebSocket(), _user())
        await mgr.connect(MockWebSocket(), _user())
        assert await mgr.broadcast("hello") == 2
    asyncio.run(_run())


def test_broadcast_removes_stale_connection():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        ws.fail_next_send(RuntimeError("connection lost"))
        await mgr.connect(ws, _user())
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
        await mgr.connect(ws_good, _user())
        await mgr.connect(ws_bad, _user())
        await mgr.broadcast("hello")
        assert ws_good.sent == ["hello"]
        assert mgr.active_count == 1
    asyncio.run(_run())


def test_broadcast_multiple_messages_in_order():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws, _user())
        await mgr.broadcast("first")
        await mgr.broadcast("second")
        assert ws.sent == ["first", "second"]
    asyncio.run(_run())


# ─── broadcast with a recipient filter ────────────────────────────────────────


def test_filter_skips_non_matching_accounts():
    async def _run():
        mgr = ConnectionManager()
        wanted, unwanted = _user(), _user()
        ws_wanted, ws_unwanted = MockWebSocket(), MockWebSocket()
        await mgr.connect(ws_wanted, wanted)
        await mgr.connect(ws_unwanted, unwanted)
        await mgr.broadcast("hello", recipient_filter=lambda uid: uid == wanted)
        assert ws_wanted.sent == ["hello"]
        assert ws_unwanted.sent == []
    asyncio.run(_run())


def test_filter_reaches_every_socket_of_a_matching_account():
    """Two tabs, one account: both are recipients."""
    async def _run():
        mgr = ConnectionManager()
        user_id = _user()
        ws1, ws2 = MockWebSocket(), MockWebSocket()
        await mgr.connect(ws1, user_id)
        await mgr.connect(ws2, user_id)
        delivered = await mgr.broadcast(
            "hello", recipient_filter=lambda uid: uid == user_id
        )
        assert delivered == 2
        assert ws1.sent == ["hello"]
        assert ws2.sent == ["hello"]
    asyncio.run(_run())


def test_filter_rejecting_everyone_delivers_nothing():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws, _user())
        delivered = await mgr.broadcast("hello", recipient_filter=lambda _uid: False)
        assert delivered == 0
        assert ws.sent == []
    asyncio.run(_run())


def test_filtered_out_connection_is_not_pruned():
    """Skipping a recipient is not the same as finding a dead socket."""
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws, _user())
        await mgr.broadcast("hello", recipient_filter=lambda _uid: False)
        assert mgr.active_count == 1
    asyncio.run(_run())


def test_explicit_none_filter_reaches_everyone():
    async def _run():
        mgr = ConnectionManager()
        ws1, ws2 = MockWebSocket(), MockWebSocket()
        await mgr.connect(ws1, _user())
        await mgr.connect(ws2, _user())
        assert await mgr.broadcast("hello", recipient_filter=None) == 2
    asyncio.run(_run())
