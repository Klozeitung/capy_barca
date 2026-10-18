"""
Tests for the database dependency and its request gate.

The gate exists for one failure: many requests arriving at once, each holding
a pooled connection while it waits for a worker thread, until every thread
waits for a connection and nothing moves. The last test reproduces that load
against a deliberately small pool.
"""
import asyncio
import uuid
from datetime import datetime, timezone

import anyio
import anyio.to_thread
import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.session.session as s
from app.blocks.models import WORKSPACE_ROOT_ID, Block
from app.database.database import POOL_CAPACITY
from app.session import deps
from app.users.model import User


# ─── Sizing ──────────────────────────────────────────────────────────────────


def test_gate_leaves_connections_for_sessions_outside_requests():
    assert deps.REQUEST_SLOTS == POOL_CAPACITY - deps.RESERVED_CONNECTIONS
    assert deps.REQUEST_SLOTS >= 1


def test_worker_threads_are_sized_above_the_gate():
    limiter = anyio.to_thread.current_default_thread_limiter
    original = None

    async def _run():
        nonlocal original
        original = limiter().total_tokens
        limiter().total_tokens = 1
        size = deps.configure_worker_threads()
        assert size == deps.REQUEST_SLOTS + deps.THREAD_HEADROOM
        assert limiter().total_tokens == size
        limiter().total_tokens = size + 10
        assert deps.configure_worker_threads() == size + 10
        limiter().total_tokens = original

    anyio.run(_run)


# ─── Admission ───────────────────────────────────────────────────────────────


def test_requests_beyond_the_slots_wait_for_a_free_one(monkeypatch):
    monkeypatch.setattr(deps, "REQUEST_SLOTS", 2)
    deps._gates.clear()

    async def _run():
        first, second, third = deps.get_db(), deps.get_db(), deps.get_db()
        await first.__anext__()
        await second.__anext__()
        waiting = asyncio.ensure_future(third.__anext__())
        await asyncio.sleep(0.05)
        assert not waiting.done()

        await first.aclose()
        await asyncio.wait_for(waiting, 1.0)
        await second.aclose()
        await third.aclose()

    asyncio.run(_run())
    deps._gates.clear()


def test_session_is_closed_when_the_request_ends():
    async def _run():
        generator = deps.get_db()
        db = await generator.__anext__()
        db.execute(select(User).limit(1))
        assert db.in_transaction()
        await generator.aclose()
        assert not db.in_transaction()

    asyncio.run(_run())


def test_session_is_closed_when_the_request_fails():
    async def _run():
        generator = deps.get_db()
        db = await generator.__anext__()
        with pytest.raises(RuntimeError):
            await generator.athrow(RuntimeError("handler failed"))
        assert not db.in_transaction()

    asyncio.run(_run())


# ─── Burst ───────────────────────────────────────────────────────────────────


def test_burst_larger_than_the_pool_does_not_stall(test_engine, monkeypatch):
    """
    Sixty simultaneous authenticated requests against a pool of three
    connections. Without the gate the worker threads fill with requests that
    wait for a connection while the connections belong to requests that wait
    for a thread, and every request runs into the pool timeout.
    """
    from app.main import app

    small_engine = create_engine(
        test_engine.url, pool_size=3, max_overflow=0, pool_timeout=3,
    )
    monkeypatch.setattr(deps, "SessionLocal", sessionmaker(bind=small_engine))
    monkeypatch.setattr(deps, "REQUEST_SLOTS", 3)
    deps._gates.clear()

    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with s.SessionLocal() as db:
        db.add(User(id=user_id, username="burst-user", password_hash="x", role="admin"))
        db.add(Block(
            id=WORKSPACE_ROOT_ID, type="workspace", position=0.0, state="active",
            content={"title": "Workspace"}, created_at=now, updated_at=now,
        ))
        db.commit()
    token = s.create_token(user_id)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", cookies={"session": token},
        ) as client:
            responses = await asyncio.wait_for(
                asyncio.gather(*(
                    client.get(f"/api/blocks/{WORKSPACE_ROOT_ID}") for _ in range(60)
                )),
                timeout=30,
            )
        return [r.status_code for r in responses]

    try:
        statuses = asyncio.run(_run())
    finally:
        deps._gates.clear()
        small_engine.dispose()

    assert statuses == [200] * 60


# ─── Actor ───────────────────────────────────────────────────────────────────


def test_current_user_is_remembered_on_the_request_session():
    """Change events of the request name this user (see app.blocks.entry_changes)."""
    from app.blocks import entry_changes

    user_id = uuid.uuid4()
    with s.SessionLocal() as db:
        db.add(User(id=user_id, username="person-a", password_hash="x", role="member"))
        db.commit()
        user = deps.get_current_user(user_id=user_id, db=db)

        assert db.info[entry_changes._ACTOR_KEY] is user
