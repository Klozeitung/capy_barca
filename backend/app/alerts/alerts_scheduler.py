"""
Alert scheduler.

Runs inside the application process. Once a minute, on the minute, it claims
every alert that has become due and notifies the account it belongs to over
the WebSocket (``alert.fired``). The first pass runs at startup, so alerts
that fell due while the server was down are handled at once.

Due rather than exact
---------------------
A pass takes every alert whose time has come and that has not fired yet,
not only those of the current minute. A restart, a deployment or a slow pass
therefore delays an alert instead of losing it. Claiming sets ``fired_at``
under ``SKIP LOCKED`` (see ``alerts_repository.claim_due``), so no alert fires
twice even if two passes overlap.

Delivery
--------
A claimed alert is delivered only when all of these hold at that moment:

* it is at most :data:`GRACE_PERIOD` overdue,
* its entry is active and is not an entry template,
* its account exists, is active and may read the entry.

Otherwise it is marked fired without being delivered. The decision is
recorded in ``delivered``; a delivered alert stays pending until the account
dismisses it, and a client that connects later collects it from
``GET /api/alerts/pending``.

System alerts of repeating calendar dates get their next occurrence once
one has fired (``app.alerts.system_alerts.sync_entry``). At startup the
system alerts are reconciled before the first pass.

Disabling
---------
``ALERT_SCHEDULER_ENABLED=false`` keeps the loop from starting. The test suite
sets it, so a background pass never runs against a test's data.
"""
import asyncio
import contextlib
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import anyio.to_thread
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerts import alerts_repository as repo
from app.alerts.alerts_models import Alert

logger = logging.getLogger(__name__)

# How long after its time an alert is still delivered.
GRACE_PERIOD = timedelta(days=7)

# WebSocket message type of a delivered alert.
FIRED_MESSAGE = "alert.fired"

# Environment switch for the loop.
ENABLED_ENV = "ALERT_SCHEDULER_ENABLED"

# Seconds past the full minute at which a pass starts, so a pass never runs
# just before the minute an alert is set to.
TICK_OFFSET_SECONDS = 1.0


def is_enabled() -> bool:
    """True unless the loop is switched off in the environment."""
    return os.getenv(ENABLED_ENV, "true").strip().lower() not in {"false", "0", "no"}


def block_title(block: Any) -> str:
    """The title of an entry, empty when it has none."""
    content = getattr(block, "content", None)
    if isinstance(content, dict):
        title = content.get("title")
        if isinstance(title, str):
            return title
    return ""


def notification_payload(alert: Alert, block: Any) -> dict[str, Any]:
    """The ``alert.fired`` payload: the alert plus what a notification shows."""
    return {
        **repo.serialize(alert),
        "block_title": block_title(block),
        "block_icon": getattr(block, "icon", None),
        "database_id": str(block.parent_id) if getattr(block, "parent_id", None) else None,
    }


def _deliverable(
    alert: Alert,
    block: Any,
    user: Any,
    effective: Any,
    now: datetime,
) -> bool:
    """Apply the delivery rules of the module docstring to one claimed alert."""
    from app.permissions.model import READ

    if now - alert.fire_at > GRACE_PERIOD:
        return False
    if block is None or block.state != "active" or block.type == "entry_template":
        return False
    if user is None or not user.is_active:
        return False
    if effective is None:
        return user.role == "admin"
    return bool(effective.admits(user, READ))


def run_pass(db: Session, now: datetime) -> list[tuple[uuid.UUID, dict[str, Any]]]:
    """
    Claim the due alerts, decide each one's delivery and commit.

    Returns ``(user_id, payload)`` for every alert to deliver; sending them is
    left to the caller, which runs on the event loop.
    """
    from app.blocks.models import Block
    from app.permissions import repository as perm_repo
    from app.users.model import User

    deliveries: list[tuple[uuid.UUID, dict[str, Any]]] = []
    while True:
        claimed = repo.claim_due(db, now)
        if not claimed:
            break

        block_ids = {a.block_id for a in claimed}
        blocks = {b.id: b for b in db.scalars(select(Block).where(Block.id.in_(block_ids)))}
        users = {
            u.id: u
            for u in db.scalars(select(User).where(User.id.in_({a.user_id for a in claimed})))
        }
        readable = [bid for bid, b in blocks.items() if b.state == "active"]
        effective = perm_repo.resolve_effective_permissions(db, readable) if readable else {}

        for alert in claimed:
            block = blocks.get(alert.block_id)
            if _deliverable(alert, block, users.get(alert.user_id), effective.get(alert.block_id), now):
                alert.delivered = True
                deliveries.append((alert.user_id, notification_payload(alert, block)))
        db.commit()
        _rearm_system_alerts(db, claimed, now)
        if len(claimed) < repo.CLAIM_BATCH:
            break
    return deliveries


def _rearm_system_alerts(db: Session, claimed: list[Alert], now: datetime) -> None:
    """Set the next occurrence's alert for every system alert that just fired."""
    from app.alerts import system_alerts
    from app.alerts.alerts_models import KIND_SYSTEM
    from app.blocks.models import PropertySchema

    keys = {(a.block_id, a.schema_id) for a in claimed if a.kind == KIND_SYSTEM}
    for block_id, schema_id in keys:
        schema = db.get(PropertySchema, schema_id)
        if schema is not None:
            system_alerts.sync_entry(db, block_id, schema, now)
    if keys:
        db.commit()


def _pass_in_own_session(now: datetime) -> list[tuple[uuid.UUID, dict[str, Any]]]:
    """Run one pass in a session of its own (worker thread)."""
    # Read at call time so the module attribute the tests redirect is used.
    import app.database.database as db_module

    with db_module.SessionLocal() as db:
        return run_pass(db, now)


async def tick(now: Optional[datetime] = None) -> int:
    """Run one pass and send its notifications; returns how many were sent."""
    from app.ws.broadcaster import send_user_message

    moment = now or datetime.now(timezone.utc)
    deliveries = await anyio.to_thread.run_sync(_pass_in_own_session, moment)
    sent = 0
    for user_id, payload in deliveries:
        sent += await send_user_message(user_id, FIRED_MESSAGE, payload)
    if deliveries:
        logger.info("Alerts delivered: %d (connections reached: %d)", len(deliveries), sent)
    return len(deliveries)


def seconds_until_next_tick(now: datetime) -> float:
    """Seconds from *now* to the next full minute plus the tick offset."""
    next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return (next_minute - now).total_seconds() + TICK_OFFSET_SECONDS


async def run_forever() -> None:
    """
    Reconcile the system alerts, then run a pass at once and once a minute
    until cancelled.
    """
    from app.alerts import system_alerts

    await anyio.to_thread.run_sync(system_alerts.reconcile_in_own_session)
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Alert pass failed; retrying at the next minute")
        await asyncio.sleep(seconds_until_next_tick(datetime.now(timezone.utc)))


def start() -> Optional["asyncio.Task[None]"]:
    """Start the loop on the running event loop, unless it is disabled."""
    if not is_enabled():
        logger.info("Alert scheduler disabled by %s", ENABLED_ENV)
        return None
    return asyncio.get_running_loop().create_task(run_forever(), name="alert-scheduler")


async def stop(task: Optional["asyncio.Task[None]"]) -> None:
    """Cancel the loop started by :func:`start` and wait for it to end."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
