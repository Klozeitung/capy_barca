"""
Unit tests for the WebSocket connection manager.

Tests are written as synchronous functions that drive async coroutines via
asyncio.run(), so no pytest-asyncio plugin or special markers are needed.
``broadcast`` only queues; ``flush`` waits until the sending tasks are done.

The manager holds no authorization logic of its own — it records which socket
belongs to which account and applies whatever filter the broadcaster hands it.
Both halves of that contract are pinned here; the rules themselves are tested
in broadcaster_test.py.
"""
import asyncio
import time
import uuid

import pytest

from app.ws import manager as manager_module
from app.ws.manager import CLOSE_TOO_SLOW, ConnectionManager


# ─── Stub ─────────────────────────────────────────────────────────────────────


class MockWebSocket:
    """Minimal WebSocket stub for testing the ConnectionManager."""

    def __init__(self) -> None:
        self.accepted: bool = False
        self.sent: list[str] = []
        self.closed_with: int | None = None
        self._raise_on_send: Exception | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        if self._raise_on_send is not None:
            raise self._raise_on_send
        self.sent.append(message)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code

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
        mgr.broadcast("hello")
        await mgr.flush()
        assert ws.sent == ["hello"]
    asyncio.run(_run())


def test_broadcast_delivers_to_all_connections():
    async def _run():
        mgr = ConnectionManager()
        ws1, ws2 = MockWebSocket(), MockWebSocket()
        await mgr.connect(ws1, _user())
        await mgr.connect(ws2, _user())
        mgr.broadcast("hello")
        await mgr.flush()
        assert ws1.sent == ["hello"]
        assert ws2.sent == ["hello"]
    asyncio.run(_run())


def test_broadcast_with_no_connections_is_noop():
    async def _run():
        mgr = ConnectionManager()
        mgr.broadcast("hello")  # must not raise
    asyncio.run(_run())


def test_broadcast_returns_the_number_queued():
    async def _run():
        mgr = ConnectionManager()
        await mgr.connect(MockWebSocket(), _user())
        await mgr.connect(MockWebSocket(), _user())
        assert mgr.broadcast("hello") == 2
    asyncio.run(_run())


def test_broadcast_removes_stale_connection():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        ws.fail_next_send(RuntimeError("connection lost"))
        await mgr.connect(ws, _user())
        mgr.broadcast("hello")
        await mgr.flush()
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
        mgr.broadcast("hello")
        await mgr.flush()
        assert ws_good.sent == ["hello"]
        assert mgr.active_count == 1
    asyncio.run(_run())


def test_broadcast_multiple_messages_in_order():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws, _user())
        mgr.broadcast("first")
        mgr.broadcast("second")
        await mgr.flush()
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
        mgr.broadcast("hello", recipient_filter=lambda uid: uid == wanted)
        await mgr.flush()
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
        delivered = mgr.broadcast(
            "hello", recipient_filter=lambda uid: uid == user_id
        )
        await mgr.flush()
        assert delivered == 2
        assert ws1.sent == ["hello"]
        assert ws2.sent == ["hello"]
    asyncio.run(_run())


def test_filter_rejecting_everyone_delivers_nothing():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws, _user())
        delivered = mgr.broadcast("hello", recipient_filter=lambda _uid: False)
        await mgr.flush()
        assert delivered == 0
        assert ws.sent == []
    asyncio.run(_run())


def test_filtered_out_connection_is_not_pruned():
    """Skipping a recipient is not the same as finding a dead socket."""
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws, _user())
        mgr.broadcast("hello", recipient_filter=lambda _uid: False)
        await mgr.flush()
        assert mgr.active_count == 1
    asyncio.run(_run())


def test_explicit_none_filter_reaches_everyone():
    async def _run():
        mgr = ConnectionManager()
        ws1, ws2 = MockWebSocket(), MockWebSocket()
        await mgr.connect(ws1, _user())
        await mgr.connect(ws2, _user())
        assert mgr.broadcast("hello", recipient_filter=None) == 2
    asyncio.run(_run())


# ─── Clients that stop reading ────────────────────────────────────────────────


class StalledWebSocket(MockWebSocket):
    """A client whose sends never complete, like one that stopped reading."""

    async def send_text(self, message: str) -> None:
        await asyncio.Event().wait()


def test_broadcast_does_not_wait_for_a_stalled_client():
    async def _run():
        mgr = ConnectionManager()
        await mgr.connect(StalledWebSocket(), _user())
        started = time.monotonic()
        for i in range(50):
            mgr.broadcast(f"message {i}")
        assert time.monotonic() - started < 0.5
    asyncio.run(_run())


def test_stalled_client_is_dropped_after_the_send_timeout(monkeypatch):
    monkeypatch.setattr(manager_module, "SEND_TIMEOUT", 0.05)

    async def _run():
        mgr = ConnectionManager()
        stalled, healthy = StalledWebSocket(), MockWebSocket()
        await mgr.connect(stalled, _user())
        await mgr.connect(healthy, _user())
        mgr.broadcast("first")
        await asyncio.sleep(0.2)
        mgr.broadcast("second")
        await mgr.flush()
        assert mgr.user_id_for(stalled) is None
        assert stalled.closed_with == CLOSE_TOO_SLOW
        assert healthy.sent == ["first", "second"]
    asyncio.run(_run())


def test_client_with_a_full_outbox_is_dropped(monkeypatch):
    monkeypatch.setattr(manager_module, "OUTBOX_LIMIT", 3)

    async def _run():
        mgr = ConnectionManager()
        stalled = StalledWebSocket()
        await mgr.connect(stalled, _user())
        # The first message is taken by the sending task and stalls there,
        # three more wait in the outbox, the fifth finds it full.
        queued = [mgr.broadcast("first")]
        await asyncio.sleep(0)
        queued += [mgr.broadcast(f"waiting {i}") for i in range(3)]
        queued.append(mgr.broadcast("one too many"))
        await asyncio.sleep(0)
        assert queued == [1, 1, 1, 1, 0]
        assert mgr.active_count == 0
        assert stalled.closed_with == CLOSE_TOO_SLOW
        assert mgr.broadcast("after the drop") == 0
    asyncio.run(_run())


def test_dropping_a_client_that_cannot_take_the_close_does_not_hang(monkeypatch):
    monkeypatch.setattr(manager_module, "SEND_TIMEOUT", 0.05)
    monkeypatch.setattr(manager_module, "CLOSE_TIMEOUT", 0.05)

    class Unclosable(StalledWebSocket):
        async def close(self, code: int = 1000) -> None:
            await asyncio.Event().wait()

    async def _run():
        mgr = ConnectionManager()
        await mgr.connect(Unclosable(), _user())
        mgr.broadcast("hello")
        await asyncio.sleep(0.3)
        assert mgr.active_count == 0
        assert not mgr._closing
    asyncio.run(_run())


def test_disconnect_discards_waiting_messages():
    async def _run():
        mgr = ConnectionManager()
        stalled = StalledWebSocket()
        await mgr.connect(stalled, _user())
        mgr.broadcast("first")
        mgr.broadcast("second")
        await asyncio.sleep(0)
        mgr.disconnect(stalled)
        await asyncio.wait_for(mgr.flush(), 1.0)
        assert mgr.active_count == 0
        assert stalled.closed_with is None
    asyncio.run(_run())


@pytest.mark.parametrize("count", [1, 5])
def test_flush_returns_once_everything_is_sent(count):
    async def _run():
        mgr = ConnectionManager()
        sockets = [MockWebSocket() for _ in range(count)]
        for websocket in sockets:
            await mgr.connect(websocket, _user())
        mgr.broadcast("hello")
        await mgr.flush()
        assert all(w.sent == ["hello"] for w in sockets)
    asyncio.run(_run())


def test_send_queues_behind_waiting_messages():
    async def _run():
        mgr = ConnectionManager()
        ws = MockWebSocket()
        await mgr.connect(ws, _user())
        mgr.broadcast("event")
        assert mgr.send(ws, "reply") is True
        await mgr.flush()
        assert ws.sent == ["event", "reply"]
    asyncio.run(_run())


def test_send_to_an_unknown_socket_is_refused():
    assert ConnectionManager().send(MockWebSocket(), "reply") is False
