"""
System alerts for calendar dates.

Every entry with a value in a date property that a calendar view uses
(``config.calendarDate``, see ``app.blocks.views``) gets one alert from the
application, of kind ``system``:

* with a time of day: 30 minutes before it begins,
* all day, one day or several: at 18:00 local time on the day before the
  first day.

A repeating date gets its alert for the next occurrence that has not begun
yet; once that alert has fired, the next one is set (see the scheduler).

Recipient and zone
------------------
The alert goes to the entry's owner, else to whoever created it (its
``created_by`` value), else to the database's owner. "Local time" is the
recipient's zone (``users.timezone``, else the installation's).

Keeping them in line
--------------------
* Every committed change of a date value runs :func:`sync_entry` for it,
  through a session hook, whichever path wrote the value: the value endpoint,
  duplicating, templates, automations, moves between databases.
* A changed calendar mark resyncs the property (:func:`resync_schema`), a
  changed account zone the account's alerts (:func:`resync_for_user`).
* At startup :func:`reconcile` brings every system alert in line and adds
  the missing ones, so nothing depends on all of the above having run, for
  example after a restore. It is idempotent.

Only the unfired system alert of an entry, date property and recipient is
ever changed; one that has fired stays as it is until it is dismissed, so its
notice is not lost when the next occurrence is set. Fired system alerts older
than the scheduler's grace period are cleaned up by :func:`reconcile`.
"""
import logging
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.alerts import recurrence

logger = logging.getLogger(__name__)

# How long before a timed date its alert fires.
LEAD_TIME = timedelta(minutes=30)

# Local time on the day before an all-day date at which its alert fires.
ALL_DAY_ALERT_TIME = time(18, 0)

# Key under which a session collects the date values its flushes touched.
_PENDING_KEY = "capybarca_system_alert_values"

UTC = timezone.utc


# ─── Times ────────────────────────────────────────────────────────────────────


def next_fire_time(
    value: Optional[dict],
    zone: Any,
    now: datetime,
    skip: Iterable[datetime] = (),
) -> Optional[datetime]:
    """
    When the system alert of the date *value* fires next, in UTC.

    Looks for the first occurrence that has not begun at *now* and whose alert
    time is not among *skip* (the times of alerts that already fired). Returns
    None when there is none, or when the value has no start.
    """
    from app.timezones import parse_time

    if not isinstance(value, dict):
        return None
    start = value.get("start")
    timed = parse_time(start, zone)
    if timed is not None:
        local = timed.astimezone(zone)
        first, clock = local.date(), local.time().replace(tzinfo=None)
    else:
        first, clock = recurrence.parse_date(start), None
        if first is None:
            return None

    skipped = {s.astimezone(UTC) for s in skip}
    today = now.astimezone(zone).date()
    for day in recurrence.occurrences(value, first, from_date=today - timedelta(days=2)):
        try:
            if clock is not None:
                begins = datetime.combine(day, clock, tzinfo=zone)
                fires = begins - LEAD_TIME
            else:
                begins = datetime.combine(day, time(0, 0), tzinfo=zone)
                fires = datetime.combine(day - timedelta(days=1), ALL_DAY_ALERT_TIME, tzinfo=zone)
            fires_utc = fires.astimezone(UTC)
        except (ValueError, OverflowError):
            continue
        if begins.astimezone(UTC) <= now:
            continue
        if fires_utc in skipped:
            continue
        return fires_utc
    return None


# ─── Recipient ────────────────────────────────────────────────────────────────


def recipient_of(db: Session, block: Any) -> Optional[uuid.UUID]:
    """The account an entry's system alert goes to, or None when none exists."""
    from sqlalchemy import select

    from app.blocks.models import Block, PropertySchema, PropertyValue
    from app.users.model import User

    candidates: list[Any] = [block.owner_id]
    created_by = db.execute(
        select(PropertyValue.value)
        .join(PropertySchema, PropertySchema.id == PropertyValue.property_schema_id)
        .where(
            PropertyValue.page_id == block.id,
            PropertySchema.type == "created_by",
        )
        .limit(1)
    ).scalar_one_or_none()
    if isinstance(created_by, dict):
        candidates.append(created_by.get("user_id"))
    parent = db.get(Block, block.parent_id) if block.parent_id else None
    if parent is not None:
        candidates.append(parent.owner_id)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            user_id = uuid.UUID(str(candidate))
        except ValueError:
            continue
        user = db.get(User, user_id)
        if user is not None and user.is_active:
            return user.id
    return None


# ─── Sync ─────────────────────────────────────────────────────────────────────


def sync_entry(
    db: Session,
    block_id: uuid.UUID,
    schema: Any,
    now: Optional[datetime] = None,
) -> None:
    """
    Bring the unfired system alert of *block_id* for the date property
    *schema* in line with the stored value: create, move or delete it.
    Flushes but does not commit.
    """
    from sqlalchemy import select

    from app.alerts.alerts_models import KIND_SYSTEM, Alert
    from app.blocks import repository as repo
    from app.blocks.models import Block
    from app.blocks.views import is_calendar_date
    from app.timezones import user_zone
    from app.users.model import User

    moment = now or datetime.now(UTC)
    rows = list(db.scalars(
        select(Alert).where(
            Alert.kind == KIND_SYSTEM,
            Alert.block_id == block_id,
            Alert.schema_id == schema.id,
        )
    ))
    open_rows = [r for r in rows if r.fired_at is None]

    desired: Optional[datetime] = None
    recipient: Optional[uuid.UUID] = None
    block = db.get(Block, block_id)
    if (
        block is not None
        and block.type != "entry_template"
        and block.parent_id == schema.database_id
        and is_calendar_date(schema)
    ):
        recipient = recipient_of(db, block)
        if recipient is not None:
            stored = repo.get_value(db, block_id, schema.id)
            fired = [r.fire_at for r in rows if r.fired_at is not None and r.user_id == recipient]
            desired = next_fire_time(
                stored.value if stored is not None else None,
                user_zone(db.get(User, recipient)),
                moment,
                skip=fired,
            )

    keep: Optional[Alert] = None
    for row in open_rows:
        if desired is not None and keep is None and row.user_id == recipient:
            keep = row
        else:
            db.delete(row)
    if desired is not None:
        if keep is None:
            db.add(Alert(
                block_id=block_id,
                schema_id=schema.id,
                user_id=recipient,
                fire_at=desired,
                title="",
                kind=KIND_SYSTEM,
            ))
        elif keep.fire_at != desired:
            keep.fire_at = desired
            keep.delivered = False
            keep.dismissed_at = None
    db.flush()


def resync_schema(db: Session, schema_id: uuid.UUID, now: Optional[datetime] = None) -> None:
    """Sync the system alerts of every entry with a value in the date property."""
    from sqlalchemy import select

    from app.alerts.alerts_models import KIND_SYSTEM, Alert
    from app.blocks.models import PropertySchema, PropertyValue

    schema = db.get(PropertySchema, schema_id)
    if schema is None:
        return
    pages = set(db.scalars(
        select(PropertyValue.page_id).where(PropertyValue.property_schema_id == schema_id)
    ))
    pages |= set(db.scalars(
        select(Alert.block_id).where(Alert.kind == KIND_SYSTEM, Alert.schema_id == schema_id)
    ))
    for page_id in pages:
        sync_entry(db, page_id, schema, now)


def resync_for_user(db: Session, user_id: uuid.UUID, now: Optional[datetime] = None) -> None:
    """Recompute the open system alerts of an account, after its zone changed."""
    from sqlalchemy import select

    from app.alerts.alerts_models import KIND_SYSTEM, Alert
    from app.blocks.models import PropertySchema

    keys = set(db.execute(
        select(Alert.block_id, Alert.schema_id).where(
            Alert.kind == KIND_SYSTEM,
            Alert.user_id == user_id,
            Alert.fired_at.is_(None),
        )
    ))
    for block_id, schema_id in keys:
        schema = db.get(PropertySchema, schema_id)
        if schema is not None:
            sync_entry(db, block_id, schema, now)


def reconcile(db: Session, now: Optional[datetime] = None) -> None:
    """
    Bring every system alert in line, add missing ones and clean up old
    fired ones. Idempotent; commits.
    """
    from sqlalchemy import delete, or_, select

    from app.alerts.alerts_models import KIND_SYSTEM, Alert
    from app.alerts.alerts_scheduler import GRACE_PERIOD
    from app.blocks.models import PropertySchema
    from app.blocks.views import CALENDAR_DATE_KEY

    moment = now or datetime.now(UTC)
    schema_ids = set(db.scalars(select(PropertySchema.id).where(PropertySchema.type == "date")))
    schema_ids |= set(db.scalars(
        select(Alert.schema_id).where(Alert.kind == KIND_SYSTEM, Alert.fired_at.is_(None))
    ))
    for schema_id in schema_ids:
        schema = db.get(PropertySchema, schema_id)
        if schema is None:
            continue
        if schema.type == "date" and (schema.config or {}).get(CALENDAR_DATE_KEY):
            resync_schema(db, schema_id, moment)
        else:
            db.execute(delete(Alert).where(
                Alert.kind == KIND_SYSTEM,
                Alert.schema_id == schema_id,
                Alert.fired_at.is_(None),
            ))
    db.execute(delete(Alert).where(
        Alert.kind == KIND_SYSTEM,
        Alert.fired_at.is_not(None),
        Alert.fired_at < moment - GRACE_PERIOD,
        or_(Alert.dismissed_at.is_not(None), Alert.delivered.is_(False)),
    ))
    db.commit()


def reconcile_in_own_session() -> None:
    """Run :func:`reconcile` in a session of its own, logging instead of raising."""
    import app.database.database as db_module

    try:
        with db_module.SessionLocal() as db:
            reconcile(db)
    except Exception:
        logger.exception("Reconciling the calendar alerts failed")


# ─── Session hook ─────────────────────────────────────────────────────────────


@event.listens_for(Session, "after_flush")
def _collect(session: Session, _flush_context: Any) -> None:
    """Remember the date values a flush wrote or deleted."""
    from app.blocks.models import PropertyValue

    touched = [
        obj for obj in (*session.new, *session.dirty, *session.deleted)
        if isinstance(obj, PropertyValue)
    ]
    if touched:
        pending = session.info.setdefault(_PENDING_KEY, set())
        pending.update((obj.page_id, obj.property_schema_id) for obj in touched)


@event.listens_for(Session, "before_commit")
def _apply(session: Session) -> None:
    """Sync the system alerts of the date values this transaction changed."""
    from sqlalchemy import select

    from app.blocks.models import PropertySchema
    from app.blocks.views import is_calendar_date

    for _ in range(5):
        session.flush()
        pending = session.info.pop(_PENDING_KEY, None)
        if not pending:
            return
        schema_ids = {schema_id for _, schema_id in pending}
        schemas = {
            s.id: s for s in session.scalars(
                select(PropertySchema).where(
                    PropertySchema.id.in_(schema_ids), PropertySchema.type == "date",
                )
            )
        }
        for page_id, schema_id in pending:
            schema = schemas.get(schema_id)
            if schema is not None and is_calendar_date(schema):
                sync_entry(session, page_id, schema)


@event.listens_for(Session, "after_rollback")
def _forget(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)
