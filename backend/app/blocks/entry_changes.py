"""
Which database entries a request changed.

``database_entries_updated`` used to say only which database changed, and
every open view answered by loading everything it showed again. To send a
delta instead, the event has to know which entries and which properties
changed. Collecting that at each of the many places that write values would
miss some, so it is collected where every write passes: the ORM flush.

A session listener records, per session:

* every property value written or deleted, as (entry, property),
* every entry whose title (``content``) changed,
* every parent whose list of children changed: a block created, deleted,
  moved, reordered, trashed or restored under it.

:func:`describe` turns that into the payload of one database's event. A
rollback forgets everything recorded so far, so only committed changes are
reported. The record is not consumed by reading it: a request that announces
the same database twice describes it the same way both times.

The request's user is kept on the session as well (:func:`set_actor`), so the
event can name who made the change.
"""
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.blocks.models import Block, PropertyValue

# Session.info keys.
_PENDING_KEY = "entry_changes"
_ACTOR_KEY = "entry_changes_actor"

# Property key that stands for an entry's title in ``entries``.
NAME_KEY = "__name__"

# Above this many changed entries the event carries no list; clients load
# what they show again instead of fetching the entries one by one.
MAX_ENTRIES = 200

# Block attributes whose change alters a parent's list of children or its order.
_LISTING_ATTRIBUTES = ("parent_id", "position", "state")


@dataclass
class _Pending:
    """Changes recorded in one session since its last rollback."""

    values: set[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=set)
    titles: set[uuid.UUID] = field(default_factory=set)
    listings: set[uuid.UUID] = field(default_factory=set)
    # Parent of every entry named in ``values`` or ``titles``, filled on demand.
    parents: dict[uuid.UUID, Optional[uuid.UUID]] = field(default_factory=dict)


def _pending(session: Session) -> _Pending:
    pending = session.info.get(_PENDING_KEY)
    if pending is None:
        pending = _Pending()
        session.info[_PENDING_KEY] = pending
    return pending


def _changed(obj: Any, attribute: str) -> bool:
    return inspect(obj).attrs[attribute].history.has_changes()


@event.listens_for(Session, "before_flush")
def _record_old_parents(session: Session, _flush_context: Any, _instances: Any) -> None:
    """
    Record the parent a block is moved away from.

    The attribute history only knows the old parent when it had been loaded
    before the change. It is read from the database instead, before the flush
    writes the new one, through the connection so no further flush starts.
    """
    moved = [
        obj.id for obj in session.dirty
        if isinstance(obj, Block) and _changed(obj, "parent_id")
    ]
    if not moved:
        return
    pending = _pending(session)
    rows = session.connection().execute(
        select(Block.parent_id).where(Block.id.in_(moved))
    )
    for (old_parent,) in rows:
        if old_parent is not None:
            pending.listings.add(old_parent)


@event.listens_for(Session, "after_flush")
def _record(session: Session, _flush_context: Any) -> None:
    pending = _pending(session)

    for obj in session.new:
        if isinstance(obj, PropertyValue):
            pending.values.add((obj.page_id, obj.property_schema_id))
        elif isinstance(obj, Block) and obj.parent_id is not None:
            pending.listings.add(obj.parent_id)

    for obj in session.dirty:
        if isinstance(obj, PropertyValue):
            if _changed(obj, "value"):
                pending.values.add((obj.page_id, obj.property_schema_id))
        elif isinstance(obj, Block):
            if _changed(obj, "content"):
                pending.titles.add(obj.id)
            if any(_changed(obj, a) for a in _LISTING_ATTRIBUTES):
                if obj.parent_id is not None:
                    pending.listings.add(obj.parent_id)

    for obj in session.deleted:
        if isinstance(obj, PropertyValue):
            pending.values.add((obj.page_id, obj.property_schema_id))
        elif isinstance(obj, Block) and obj.parent_id is not None:
            pending.listings.add(obj.parent_id)


@event.listens_for(Session, "after_rollback")
def _forget(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


def set_actor(session: Session, user: Any) -> None:
    """Remember the user on whose behalf *session* writes."""
    session.info[_ACTOR_KEY] = user


def _resolve_parents(session: Session, pending: _Pending) -> None:
    """Look up the parent of every recorded entry not looked up yet."""
    wanted = {page_id for page_id, _ in pending.values} | pending.titles
    missing = [i for i in wanted if i not in pending.parents]
    if not missing:
        return
    found = dict(session.execute(
        select(Block.id, Block.parent_id).where(Block.id.in_(missing))
    ).all())
    for block_id in missing:
        # A block that no longer exists was purged; its parent's listing
        # change is recorded separately.
        pending.parents[block_id] = found.get(block_id)


def describe(session: Session, database_id: Any) -> dict:
    """
    Event details for *database_id* from what *session* recorded.

    Returns a dict to merge into the event's ``after``:

    * ``entries`` maps each changed entry of the database to the properties
      that changed on it (``NAME_KEY`` for its title). Left out when nothing
      was recorded for the database or when more than ``MAX_ENTRIES`` entries
      changed; clients then load what they show again.
    * ``listing`` is true when entries were added, removed or reordered.
    * ``actor`` names the user, when one is known: ``{"id", "name"}``.
    """
    details: dict = {}
    actor = session.info.get(_ACTOR_KEY)
    if actor is not None:
        details["actor"] = {"id": str(actor.id), "name": actor.username}

    pending = session.info.get(_PENDING_KEY)
    if pending is None:
        return details
    try:
        target = uuid.UUID(str(database_id))
    except ValueError:
        return details

    _resolve_parents(session, pending)
    entries: dict[str, set[str]] = {}
    for page_id, schema_id in pending.values:
        if pending.parents.get(page_id) == target:
            entries.setdefault(str(page_id), set()).add(str(schema_id))
    for block_id in pending.titles:
        if pending.parents.get(block_id) == target:
            entries.setdefault(str(block_id), set()).add(NAME_KEY)

    listing = target in pending.listings
    if listing:
        details["listing"] = True
    if not entries and not listing:
        # Nothing recorded here: the change came from somewhere this session
        # did not see, so clients have to load again.
        return details
    if len(entries) <= MAX_ENTRIES:
        details["entries"] = {k: sorted(v) for k, v in entries.items()}
    return details
