"""
Alert repository.

All database access for the alerts module. Nothing here commits; the router
and the scheduler own their transactions.

Cell values
-----------
:func:`values_for_entries` produces what the entry endpoints put into
``values`` for an ``alerts`` property: ``{"alerts": [<alert>, ...]}`` with the
alerts of one account only, ordered by time. The same shape goes out with the
``alert.changed`` WebSocket message, so a client replaces the cell with it.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.alerts.alerts_models import ALERTS_PROPERTY_TYPE, Alert
from app.blocks.models import Block, PropertySchema

# Alerts claimed by one scheduler pass at most; the rest follow in the next.
CLAIM_BATCH = 500


# ─── Serialisation ────────────────────────────────────────────────────────────


def iso_utc(moment: Optional[datetime]) -> Optional[str]:
    """Return *moment* as ISO 8601 in UTC with a ``Z`` suffix, or None."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def serialize(alert: Alert) -> dict[str, Any]:
    """One alert in the shape every endpoint and message uses."""
    return {
        "id": str(alert.id),
        "block_id": str(alert.block_id),
        "schema_id": str(alert.schema_id),
        "at": iso_utc(alert.fire_at),
        "title": alert.title,
        "fired_at": iso_utc(alert.fired_at),
        "delivered": alert.delivered,
        "dismissed_at": iso_utc(alert.dismissed_at),
    }


def cell_value(alerts: Iterable[Alert]) -> dict[str, Any]:
    """The value of an ``alerts`` cell holding *alerts*, ordered by time."""
    ordered = sorted(alerts, key=lambda a: (a.fire_at, str(a.id)))
    return {"alerts": [serialize(a) for a in ordered]}


# ─── Property schema ──────────────────────────────────────────────────────────


def find_alerts_schema(db: Session, database_id: uuid.UUID) -> Optional[PropertySchema]:
    """The ``alerts`` property of *database_id*, or None; there is at most one."""
    return db.scalars(
        select(PropertySchema)
        .where(
            PropertySchema.database_id == database_id,
            PropertySchema.type == ALERTS_PROPERTY_TYPE,
        )
        .order_by(PropertySchema.position)
        .limit(1)
    ).first()


# ─── Read ─────────────────────────────────────────────────────────────────────


def get_alert(db: Session, alert_id: uuid.UUID) -> Optional[Alert]:
    return db.get(Alert, alert_id)


def list_for_entry(db: Session, user_id: uuid.UUID, block_id: uuid.UUID) -> list[Alert]:
    """The alerts *user_id* keeps on *block_id*."""
    return list(db.scalars(
        select(Alert).where(Alert.user_id == user_id, Alert.block_id == block_id)
    ))


def count_for_entry(db: Session, user_id: uuid.UUID, block_id: uuid.UUID) -> int:
    return len(list_for_entry(db, user_id, block_id))


def values_for_entries(
    db: Session,
    user_id: Optional[uuid.UUID],
    entries: Iterable[Block],
) -> dict[uuid.UUID, dict[str, dict[str, Any]]]:
    """
    The ``alerts`` cell values of *user_id* for *entries*.

    Returns ``{entry_id: {schema_id: cell value}}`` for every entry whose
    database has an ``alerts`` property. The value holds that account's
    alerts only, and is empty without *user_id*. Because every such cell is
    present, it takes the place of anything ``property_values`` might hold for
    the property. Two queries, however many entries.
    """
    entry_list = list(entries)
    database_ids = {e.parent_id for e in entry_list if e.parent_id is not None}
    if not database_ids:
        return {}
    schema_by_database = {
        database_id: schema_id
        for schema_id, database_id in db.execute(
            select(PropertySchema.id, PropertySchema.database_id).where(
                PropertySchema.database_id.in_(database_ids),
                PropertySchema.type == ALERTS_PROPERTY_TYPE,
            )
        )
    }
    relevant = [e for e in entry_list if e.parent_id in schema_by_database]
    if not relevant:
        return {}

    by_entry: dict[uuid.UUID, list[Alert]] = {e.id: [] for e in relevant}
    if user_id is not None:
        for alert in db.scalars(
            select(Alert).where(
                Alert.user_id == user_id,
                Alert.block_id.in_(list(by_entry)),
            )
        ):
            by_entry[alert.block_id].append(alert)

    return {
        entry.id: {str(schema_by_database[entry.parent_id]): cell_value(by_entry[entry.id])}
        for entry in relevant
    }


def list_upcoming(db: Session, user_id: uuid.UUID, limit: int) -> list[Alert]:
    """The alerts of *user_id* not fired yet, earliest first, on active entries."""
    return list(db.scalars(
        select(Alert)
        .join(Block, Block.id == Alert.block_id)
        .where(
            Alert.user_id == user_id,
            Alert.fired_at.is_(None),
            Block.state == "active",
        )
        .order_by(Alert.fire_at, Alert.id)
        .limit(limit)
    ))


def list_pending(db: Session, user_id: uuid.UUID, limit: int) -> list[Alert]:
    """The delivered alerts of *user_id* not dismissed yet, earliest first."""
    return list(db.scalars(
        select(Alert)
        .where(
            Alert.user_id == user_id,
            Alert.delivered.is_(True),
            Alert.dismissed_at.is_(None),
        )
        .order_by(Alert.fire_at, Alert.id)
        .limit(limit)
    ))


# ─── Write ────────────────────────────────────────────────────────────────────


def create_alert(
    db: Session,
    *,
    block_id: uuid.UUID,
    schema_id: uuid.UUID,
    user_id: uuid.UUID,
    fire_at: datetime,
    title: str,
) -> Alert:
    alert = Alert(
        block_id=block_id,
        schema_id=schema_id,
        user_id=user_id,
        fire_at=fire_at,
        title=title,
    )
    db.add(alert)
    db.flush()
    return alert


def update_alert(
    db: Session,
    alert: Alert,
    *,
    fire_at: Optional[datetime] = None,
    title: Optional[str] = None,
) -> Alert:
    """
    Change the time or title of *alert*.

    A new time makes the alert due again: its firing and dismissal are
    forgotten. A changed title alone keeps them.
    """
    if fire_at is not None and fire_at != alert.fire_at:
        alert.fire_at = fire_at
        alert.fired_at = None
        alert.delivered = False
        alert.dismissed_at = None
    if title is not None:
        alert.title = title
    db.flush()
    return alert


def delete_alert(db: Session, alert: Alert) -> None:
    db.delete(alert)
    db.flush()


def dismiss_alert(db: Session, alert: Alert, now: datetime) -> Alert:
    """Record that the account closed the notification; idempotent."""
    if alert.dismissed_at is None:
        alert.dismissed_at = now
        db.flush()
    return alert


def delete_for_schema(db: Session, schema_id: uuid.UUID) -> int:
    """Delete every alert of the property *schema_id*; returns how many."""
    alerts = list(db.scalars(select(Alert).where(Alert.schema_id == schema_id)))
    for alert in alerts:
        db.delete(alert)
    db.flush()
    return len(alerts)


def transfer_to_database(
    db: Session, block_ids: Iterable[uuid.UUID], database_id: Optional[uuid.UUID]
) -> None:
    """
    Follow entries that moved into *database_id*.

    Their alerts go over to the ``alerts`` property of that database. When it
    has none, or the blocks left databases altogether (*database_id* None),
    the alerts are deleted.
    """
    ids = list(block_ids)
    if not ids:
        return
    alerts = list(db.scalars(select(Alert).where(Alert.block_id.in_(ids))))
    if not alerts:
        return
    target = find_alerts_schema(db, database_id) if database_id is not None else None
    for alert in alerts:
        if target is None:
            db.delete(alert)
        elif alert.schema_id != target.id:
            alert.schema_id = target.id
    db.flush()


def claim_due(db: Session, now: datetime, limit: int = CLAIM_BATCH) -> list[Alert]:
    """
    Mark up to *limit* due alerts as fired and return them.

    The rows are locked with ``SKIP LOCKED`` while they are claimed, so two
    passes running at once (a second process, an overlapping run) never claim
    the same alert. Whether a claimed alert is delivered is decided by the
    caller, which sets ``delivered``.
    """
    due = (
        select(Alert.id)
        .where(Alert.fired_at.is_(None), Alert.fire_at <= now)
        .order_by(Alert.fire_at, Alert.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    claimed_ids = list(db.scalars(
        update(Alert)
        .where(Alert.id.in_(due))
        .values(fired_at=now)
        .returning(Alert.id)
    ))
    if not claimed_ids:
        return []
    return list(db.scalars(
        select(Alert)
        .where(Alert.id.in_(claimed_ids))
        .order_by(Alert.fire_at, Alert.id)
        .execution_options(populate_existing=True)
    ))
