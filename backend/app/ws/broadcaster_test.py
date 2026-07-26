"""
Tests for the event broadcaster's recipient scoping.

The broadcaster is the only place that decides who may see a block event, so
this is where the rules are pinned. The plumbing it drives — the connection
registry and the filter mechanism — is tested in manager_test.py.

Tests are synchronous functions driving coroutines through asyncio.run(), the
same style as manager_test.py, so no pytest-asyncio marker is needed. The
``isolated_db`` fixture is autouse and supplies the in-memory database the
permission lookups run against.
"""
import asyncio
import json
import uuid

import pytest

import app.session.session as s
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.permissions import repository as perm_repo
from app.users.model import User
from app.ws.broadcaster import broadcast_block_event
from app.ws.manager import manager


# ─── Stub ─────────────────────────────────────────────────────────────────────


class MockWebSocket:
    """Minimal WebSocket stub; mirrors the one in manager_test.py."""

    def __init__(self) -> None:
        self.accepted: bool = False
        self.sent: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        self.sent.append(message)

    @property
    def events(self) -> list[dict]:
        """Decoded payloads of everything this socket received."""
        return [json.loads(m)["payload"] for m in self.sent]


# ─── Fixtures and helpers ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_manager():
    """
    Empty the module-level singleton around every test.

    The registry is process-wide by design — one manager serves the whole
    application — so tests have to reset it rather than build their own.
    """
    manager._connections.clear()
    yield
    manager._connections.clear()


def _make_user(role: str = "member", is_active: bool = True) -> User:
    user = User(
        id=uuid.uuid4(),
        username=f"user_{uuid.uuid4().hex[:8]}",
        password_hash="x",
        role=role,
        is_active=is_active,
    )
    with s.SessionLocal() as db:
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
    return user


def _seed_root() -> None:
    with s.SessionLocal() as db:
        db.merge(Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0, state="active"))
        db.commit()


def _seed_block(owner_id=None, mode: str = "private", grants=()) -> uuid.UUID:
    block_id = uuid.uuid4()
    with s.SessionLocal() as db:
        block = Block(
            id=block_id,
            parent_id=WORKSPACE_ROOT_ID,
            type="page",
            position=1.0,
            state="active",
        )
        block.owner_id = owner_id
        db.add(block)
        db.flush()
        perm_repo.set_permission(db, block_id, mode, list(grants))
        db.commit()
    return block_id


async def _attach(user: User) -> MockWebSocket:
    """Register a fresh socket for *user* and return it."""
    websocket = MockWebSocket()
    await manager.connect(websocket, user.id)
    return websocket


async def _emit(block_id, *, event_type: str = "content_updated") -> int:
    return await broadcast_block_event(
        event_type=event_type,
        block_id=str(block_id) if block_id is not None else None,
        before={"content": {"title": "secret"}},
        after={"content": {"title": "still secret"}},
    )


# ─── Envelope ─────────────────────────────────────────────────────────────────


def test_envelope_shape_is_unchanged():
    async def _run():
        _seed_root()
        owner = _make_user()
        block_id = _seed_block(owner_id=owner.id)
        websocket = await _attach(owner)
        await _emit(block_id)
        envelope = json.loads(websocket.sent[0])
        assert envelope["type"] == "block.event"
        assert set(envelope["payload"]) == {
            "event_id", "event_type", "block_id", "before", "after", "created_at",
        }
    asyncio.run(_run())


def test_no_connections_returns_zero():
    async def _run():
        _seed_root()
        block_id = _seed_block(owner_id=uuid.uuid4())
        assert await _emit(block_id) == 0
    asyncio.run(_run())


# ─── Per-recipient scoping ────────────────────────────────────────────────────


def test_owner_receives_the_event():
    async def _run():
        _seed_root()
        owner = _make_user()
        block_id = _seed_block(owner_id=owner.id, mode="private")
        websocket = await _attach(owner)
        assert await _emit(block_id) == 1
        assert len(websocket.events) == 1
    asyncio.run(_run())


def test_foreign_member_receives_nothing():
    """The case the whole batch exists for: no content to an unauthorised socket."""
    async def _run():
        _seed_root()
        stranger = _make_user()
        block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
        websocket = await _attach(stranger)
        assert await _emit(block_id) == 0
        assert websocket.sent == []
    asyncio.run(_run())


def test_owner_receives_while_stranger_does_not():
    async def _run():
        _seed_root()
        owner, stranger = _make_user(), _make_user()
        block_id = _seed_block(owner_id=owner.id, mode="private")
        ws_owner = await _attach(owner)
        ws_stranger = await _attach(stranger)
        assert await _emit(block_id) == 1
        assert len(ws_owner.events) == 1
        assert ws_stranger.sent == []
    asyncio.run(_run())


def test_admin_receives_a_foreign_private_block():
    async def _run():
        _seed_root()
        admin = _make_user(role="admin")
        block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
        websocket = await _attach(admin)
        assert await _emit(block_id) == 1
        assert len(websocket.events) == 1
    asyncio.run(_run())


def test_whitelisted_member_receives():
    async def _run():
        _seed_root()
        member = _make_user()
        block_id = _seed_block(
            owner_id=uuid.uuid4(), mode="whitelist", grants=[member.id]
        )
        websocket = await _attach(member)
        assert await _emit(block_id) == 1
        assert len(websocket.events) == 1
    asyncio.run(_run())


def test_member_without_grant_receives_nothing():
    async def _run():
        _seed_root()
        member = _make_user()
        block_id = _seed_block(owner_id=uuid.uuid4(), mode="whitelist", grants=[])
        websocket = await _attach(member)
        assert await _emit(block_id) == 0
        assert websocket.sent == []
    asyncio.run(_run())


def test_everyone_mode_reaches_any_member():
    async def _run():
        _seed_root()
        member = _make_user()
        block_id = _seed_block(owner_id=uuid.uuid4(), mode="everyone")
        await _attach(member)
        assert await _emit(block_id) == 1
    asyncio.run(_run())


def test_every_socket_of_an_admitted_account_receives():
    async def _run():
        _seed_root()
        owner = _make_user()
        block_id = _seed_block(owner_id=owner.id)
        ws1 = await _attach(owner)
        ws2 = await _attach(owner)
        assert await _emit(block_id) == 2
        assert len(ws1.events) == 1
        assert len(ws2.events) == 1
    asyncio.run(_run())


# ─── Account state is resolved per send, not at connect time ──────────────────


def test_deactivated_account_receives_nothing():
    """A socket held open across a deactivation must go quiet immediately."""
    async def _run():
        _seed_root()
        owner = _make_user()
        block_id = _seed_block(owner_id=owner.id)
        websocket = await _attach(owner)

        with s.SessionLocal() as db:
            db.get(User, owner.id).is_active = False
            db.commit()

        assert await _emit(block_id) == 0
        assert websocket.sent == []
    asyncio.run(_run())


def test_deleted_account_receives_nothing():
    async def _run():
        _seed_root()
        owner = _make_user()
        block_id = _seed_block(owner_id=owner.id)
        websocket = await _attach(owner)

        with s.SessionLocal() as db:
            db.delete(db.get(User, owner.id))
            db.commit()

        assert await _emit(block_id) == 0
        assert websocket.sent == []
    asyncio.run(_run())


def test_access_lost_mid_session_stops_delivery():
    async def _run():
        _seed_root()
        member = _make_user()
        block_id = _seed_block(
            owner_id=uuid.uuid4(), mode="whitelist", grants=[member.id]
        )
        websocket = await _attach(member)
        assert await _emit(block_id) == 1

        with s.SessionLocal() as db:
            perm_repo.set_permission(db, block_id, "whitelist", [])
            db.commit()

        assert await _emit(block_id) == 0
        assert len(websocket.events) == 1
    asyncio.run(_run())


# ─── Events with no block ─────────────────────────────────────────────────────


def test_workspace_level_event_reaches_admins_only():
    async def _run():
        _seed_root()
        admin, member = _make_user(role="admin"), _make_user()
        ws_admin = await _attach(admin)
        ws_member = await _attach(member)
        assert await _emit(None) == 1
        assert len(ws_admin.events) == 1
        assert ws_member.sent == []
    asyncio.run(_run())


def test_unparseable_block_id_reaches_admins_only():
    """A malformed id names no object, so it is treated as workspace-level."""
    async def _run():
        _seed_root()
        admin, member = _make_user(role="admin"), _make_user()
        ws_admin = await _attach(admin)
        ws_member = await _attach(member)
        delivered = await broadcast_block_event(
            event_type="content_updated",
            block_id="not-a-uuid",
            before=None,
            after={"content": {"title": "secret"}},
        )
        assert delivered == 1
        assert len(ws_admin.events) == 1
        assert ws_member.sent == []
    asyncio.run(_run())


# ─── Purge ────────────────────────────────────────────────────────────────────


def test_purge_reaches_an_account_without_access():
    """
    After a purge there is no permission row left to evaluate, and clients need
    the id to drop the block from their view. The payload carries no content.
    """
    async def _run():
        _seed_root()
        stranger = _make_user()
        websocket = await _attach(stranger)
        delivered = await broadcast_block_event(
            event_type="purged",
            block_id=str(uuid.uuid4()),
            before=None,
            after=None,
        )
        assert delivered == 1
        payload = websocket.events[0]
        assert payload["before"] is None
        assert payload["after"] is None
    asyncio.run(_run())


def test_purge_does_not_reach_a_deactivated_account():
    """The purge exception covers permissions, not authentication."""
    async def _run():
        _seed_root()
        user = _make_user(is_active=False)
        websocket = await _attach(user)
        delivered = await broadcast_block_event(
            event_type="purged",
            block_id=str(uuid.uuid4()),
            before=None,
            after=None,
        )
        assert delivered == 0
        assert websocket.sent == []
    asyncio.run(_run())


def test_soft_delete_is_still_scoped():
    """
    Trashing is not purging: the block and its permission row both survive, so
    the ordinary rule applies.
    """
    async def _run():
        _seed_root()
        stranger = _make_user()
        block_id = _seed_block(owner_id=uuid.uuid4(), mode="private")
        websocket = await _attach(stranger)
        delivered = await broadcast_block_event(
            event_type="state_changed",
            block_id=str(block_id),
            before={"state": "active"},
            after={"state": "trash"},
        )
        assert delivered == 0
        assert websocket.sent == []
    asyncio.run(_run())
