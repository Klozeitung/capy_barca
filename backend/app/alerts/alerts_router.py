"""
Alerts router.

Alerts are personal: every endpoint acts on the caller's own alerts only. An
alert of another account answers 404, the same as one that does not exist, so
its existence is not disclosed.

Authorization
-------------
Read access to the entry is enough to keep an alert on it; the alert is a
reminder of the caller and changes nothing others see. Read access is checked
when an alert is created or changed, and again by the scheduler when it fires
(see ``app.alerts.alerts_scheduler``).

Validation
----------
``at`` must carry a time zone and lie in the future (422 otherwise). The
entry has to be an active entry of a database that has an ``alerts``
property. Titles are trimmed and limited to ``MAX_TITLE_LENGTH`` characters,
and an account keeps at most ``MAX_ALERTS_PER_ENTRY`` alerts on one entry.

After every change the caller's other connections receive ``alert.changed``
with the entry's new cell value, and after a dismissal ``alert.dismissed``.

System alerts of calendar dates (``kind`` ``system``, see
``app.alerts.system_alerts``) belong to their recipient too. They appear in
``pending`` and ``upcoming`` and can be dismissed, but not changed or deleted
here; they follow the date.

GET    /api/alerts/pending           delivered alerts not dismissed yet
GET    /api/alerts/upcoming          alerts not fired yet, earliest first
GET    /api/alerts/{id}              one alert
POST   /api/alerts                   create an alert
POST   /api/alerts/shift             move the caller's alerts with a moved date
PATCH  /api/alerts/{id}              change time or title
DELETE /api/alerts/{id}              delete an alert
POST   /api/alerts/{id}/dismiss      close a delivered alert
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy.orm import Session

from app.alerts import alerts_repository as repo
from app import timezones
from app.alerts.alerts_models import KIND_USER, MAX_ALERTS_PER_ENTRY, MAX_TITLE_LENGTH, Alert
from app.alerts.alerts_scheduler import notification_payload
from app.blocks.models import Block
from app.permissions import repository as perm_repo
from app.permissions.model import READ
from app.session.deps import get_current_user, get_db, require_block_access
from app.users.model import User
from app.ws.broadcaster import emit_user_message

alerts_router = APIRouter(prefix="/api/alerts", tags=["alerts"])

# WebSocket message types sent to the caller's own connections.
CHANGED_MESSAGE = "alert.changed"
DISMISSED_MESSAGE = "alert.dismissed"

# Most alerts a listing returns.
LIST_LIMIT = 500


# ─── Request / Response schemas ───────────────────────────────────────────────


class AlertCreate(BaseModel):
    block_id: uuid.UUID
    at: AwareDatetime
    title: str = ""


class AlertUpdate(BaseModel):
    at: Optional[AwareDatetime] = None
    title: Optional[str] = None


class AlertShift(BaseModel):
    """
    A date of *block_id* moved from ``old_start`` to ``new_start``: local
    ``YYYY-MM-DDTHH:MM`` or all-day ``YYYY-MM-DD`` as the client shows them,
    read in the zone of the request. ``day`` keeps each alert's time of day
    and its distance in days, ``exact`` its exact distance.
    """

    block_id: uuid.UUID
    old_start: str = Field(min_length=8, max_length=40)
    new_start: str = Field(min_length=8, max_length=40)
    mode: Literal["day", "exact"]


class AlertResponse(BaseModel):
    id: uuid.UUID
    block_id: uuid.UUID
    schema_id: uuid.UUID
    at: str
    title: str
    fired_at: Optional[str] = None
    delivered: bool
    dismissed_at: Optional[str] = None
    kind: str = KIND_USER


class AlertNotification(AlertResponse):
    """An alert with what a notification of it shows."""

    block_title: str
    block_icon: Optional[str] = None
    database_id: Optional[uuid.UUID] = None


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_title(title: str) -> str:
    cleaned = title.strip()
    if len(cleaned) > MAX_TITLE_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Title longer than {MAX_TITLE_LENGTH} characters",
        )
    return cleaned


def _future(at: datetime) -> datetime:
    moment = at.astimezone(timezone.utc)
    if moment <= _now():
        raise HTTPException(status_code=422, detail="Alert time lies in the past")
    return moment


def _own_alert(
    db: Session, alert_id: uuid.UUID, user: User, *, user_kind_only: bool = True,
) -> Alert:
    """
    The caller's alert *alert_id*; 404 for a missing or foreign one, and,
    unless *user_kind_only* is False, for a system alert.
    """
    alert = repo.get_alert(db, alert_id)
    if alert is None or alert.user_id != user.id:
        raise HTTPException(status_code=404, detail="Alert not found")
    if user_kind_only and alert.kind != KIND_USER:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


def _start_instant(value: str, zone) -> datetime:
    """A date's start as an instant: its time of day, or local midnight."""
    from app.alerts.recurrence import parse_date

    timed = timezones.parse_time(value, zone)
    if timed is not None:
        return timed
    day = parse_date(value)
    if day is None:
        raise HTTPException(status_code=422, detail="Unreadable date")
    return datetime.combine(day, datetime.min.time(), tzinfo=zone)


def _entry_with_alerts_schema(db: Session, block_id: uuid.UUID) -> tuple[Block, uuid.UUID]:
    """The active entry *block_id* and the id of its database's alerts property."""
    block = db.get(Block, block_id)
    if block is None or block.state != "active":
        raise HTTPException(status_code=404, detail="Entry not found")
    parent = db.get(Block, block.parent_id) if block.parent_id else None
    if block.type == "entry_template" or parent is None or parent.type != "database":
        raise HTTPException(status_code=422, detail="Alerts can only be set on database entries")
    schema = repo.find_alerts_schema(db, parent.id)
    if schema is None:
        raise HTTPException(status_code=422, detail="The database has no alerts property")
    return block, schema.id


def _readable(db: Session, alerts: list[Alert], user: User) -> list[Alert]:
    """The alerts among *alerts* whose entry *user* may still read."""
    allowed = perm_repo.filter_accessible(db, {a.block_id for a in alerts}, user, level=READ)
    return [a for a in alerts if a.block_id in allowed]


def _announce_change(db: Session, user: User, block_id: uuid.UUID) -> None:
    """Send the entry's new cell value to the caller's connections."""
    emit_user_message(user.id, CHANGED_MESSAGE, repo.change_payload(db, user.id, block_id), db=db)


# ─── Endpoints ────────────────────────────────────────────────────────────────


@alerts_router.get("/pending", response_model=list[AlertNotification])
def list_pending(
    limit: int = Query(default=LIST_LIMIT, ge=1, le=LIST_LIMIT),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Delivered alerts of the caller not dismissed yet, on entries still readable."""
    alerts = _readable(db, repo.list_pending(db, current_user.id, limit), current_user)
    blocks = {a.block_id: db.get(Block, a.block_id) for a in alerts}
    return [notification_payload(a, blocks[a.block_id]) for a in alerts]


@alerts_router.get("/upcoming", response_model=list[AlertNotification])
def list_upcoming(
    limit: int = Query(default=LIST_LIMIT, ge=1, le=LIST_LIMIT),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """
    Alerts of the caller not fired yet, earliest first, on entries still readable.

    For clients that schedule notifications on the device themselves.
    """
    alerts = _readable(db, repo.list_upcoming(db, current_user.id, limit), current_user)
    blocks = {a.block_id: db.get(Block, a.block_id) for a in alerts}
    return [notification_payload(a, blocks[a.block_id]) for a in alerts]


@alerts_router.post("/shift", response_model=list[AlertResponse])
def shift_alerts(
    payload: AlertShift,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """
    Move the caller's user alerts on an entry along with its moved date.

    Other accounts' alerts stay where they are, and so do system alerts,
    which follow the date on their own. An alert that ends up in the past
    counts as delivered without a notice; one in the future is due again.
    """
    block = db.get(Block, payload.block_id)
    if block is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    require_block_access(db, block.id, current_user, level=READ)
    zone = timezones.zone_of(db)
    alerts = repo.list_for_entry(db, current_user.id, block.id)
    repo.shift_alerts(
        alerts,
        mode=payload.mode,
        old_start=_start_instant(payload.old_start, zone),
        new_start=_start_instant(payload.new_start, zone),
        zone=zone,
        now=_now(),
    )
    db.commit()
    _announce_change(db, current_user, block.id)
    return [repo.serialize(a) for a in repo.list_for_entry(db, current_user.id, block.id)]


@alerts_router.get("/{alert_id}", response_model=AlertResponse)
def get_alert(
    alert_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    alert = _own_alert(db, alert_id, current_user, user_kind_only=False)
    require_block_access(db, alert.block_id, current_user, level=READ)
    return repo.serialize(alert)


@alerts_router.post("", response_model=AlertResponse, status_code=201)
def create_alert(
    payload: AlertCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    block, schema_id = _entry_with_alerts_schema(db, payload.block_id)
    require_block_access(db, block.id, current_user, level=READ)
    fire_at = _future(payload.at)
    title = _clean_title(payload.title)
    if repo.count_for_entry(db, current_user.id, block.id) >= MAX_ALERTS_PER_ENTRY:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_ALERTS_PER_ENTRY} alerts per entry",
        )
    alert = repo.create_alert(
        db,
        block_id=block.id,
        schema_id=schema_id,
        user_id=current_user.id,
        fire_at=fire_at,
        title=title,
    )
    db.commit()
    db.refresh(alert)
    _announce_change(db, current_user, block.id)
    return repo.serialize(alert)


@alerts_router.patch("/{alert_id}", response_model=AlertResponse)
def update_alert(
    alert_id: uuid.UUID,
    payload: AlertUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    alert = _own_alert(db, alert_id, current_user)
    require_block_access(db, alert.block_id, current_user, level=READ)
    fire_at = _future(payload.at) if payload.at is not None else None
    title = _clean_title(payload.title) if payload.title is not None else None
    repo.update_alert(db, alert, fire_at=fire_at, title=title)
    db.commit()
    db.refresh(alert)
    _announce_change(db, current_user, alert.block_id)
    return repo.serialize(alert)


@alerts_router.delete("/{alert_id}", status_code=204)
def delete_alert(
    alert_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete one of the caller's alerts; read access is not required to remove it."""
    alert = _own_alert(db, alert_id, current_user)
    block_id = alert.block_id
    was_pending = alert.delivered and alert.dismissed_at is None
    repo.delete_alert(db, alert)
    db.commit()
    _announce_change(db, current_user, block_id)
    if was_pending:
        emit_user_message(current_user.id, DISMISSED_MESSAGE, {"id": str(alert_id)}, db=db)


@alerts_router.post("/{alert_id}/dismiss", response_model=AlertResponse)
def dismiss_alert(
    alert_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Close a delivered alert on every connection of the caller, system alerts included."""
    alert = _own_alert(db, alert_id, current_user, user_kind_only=False)
    repo.dismiss_alert(db, alert, _now())
    db.commit()
    db.refresh(alert)
    emit_user_message(current_user.id, DISMISSED_MESSAGE, {"id": str(alert.id)}, db=db)
    _announce_change(db, current_user, alert.block_id)
    return repo.serialize(alert)
