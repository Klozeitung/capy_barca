"""
Block event emitter.

Responsible for creating :class:`BlockEvent` records that capture
before/after snapshots of block mutations. All service-layer operations
that mutate a block call the appropriate helper here.

Keeping event creation in a dedicated module prevents the service layer
from importing models directly for this purpose and makes the emission
logic independently testable.
"""
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.blocks.models import BlockEvent


def _emit(
    db: Session,
    *,
    block_id: Optional[uuid.UUID],
    event_type: str,
    before: Optional[dict],
    after: Optional[dict],
) -> BlockEvent:
    """
    Insert a :class:`BlockEvent` record and flush it to the session.

    Parameters
    ----------
    db:
        Active database session.
    block_id:
        UUID of the block this event belongs to. May be ``None`` when the
        block has already been deleted.
    event_type:
        Semantic event label, e.g. ``"content_updated"``.
    before:
        Snapshot of the mutated fields before the change. ``None`` for
        creation events.
    after:
        Snapshot of the mutated fields after the change. ``None`` for
        deletion events.
    """
    event = BlockEvent(
        block_id=block_id,
        event_type=event_type,
        before=before,
        after=after,
    )
    db.add(event)
    db.flush()
    return event


def emit_created(db: Session, block_id: uuid.UUID, snapshot: dict) -> BlockEvent:
    """Emit a ``created`` event with the initial block snapshot."""
    return _emit(db, block_id=block_id, event_type="created", before=None, after=snapshot)


def emit_content_updated(
    db: Session,
    block_id: uuid.UUID,
    before: Optional[Any],
    after: Optional[Any],
) -> BlockEvent:
    """Emit a ``content_updated`` event."""
    return _emit(
        db,
        block_id=block_id,
        event_type="content_updated",
        before={"content": before},
        after={"content": after},
    )


def emit_icon_changed(
    db: Session,
    block_id: uuid.UUID,
    before: Optional[str],
    after: Optional[str],
) -> BlockEvent:
    """Emit an ``icon_changed`` event."""
    return _emit(
        db,
        block_id=block_id,
        event_type="icon_changed",
        before={"icon": before},
        after={"icon": after},
    )


def emit_cover_changed(
    db: Session,
    block_id: uuid.UUID,
    before: Optional[str],
    after: Optional[str],
) -> BlockEvent:
    """Emit a ``cover_changed`` event."""
    return _emit(
        db,
        block_id=block_id,
        event_type="cover_changed",
        before={"cover": before},
        after={"cover": after},
    )


def emit_moved(
    db: Session,
    block_id: uuid.UUID,
    before_parent_id: Optional[uuid.UUID],
    after_parent_id: uuid.UUID,
    before_position: float,
    after_position: float,
) -> BlockEvent:
    """Emit a ``moved`` event."""
    return _emit(
        db,
        block_id=block_id,
        event_type="moved",
        before={
            "parent_id": str(before_parent_id) if before_parent_id else None,
            "position": before_position,
        },
        after={
            "parent_id": str(after_parent_id),
            "position": after_position,
        },
    )


def emit_block_updated(
    db: Session,
    block_id: uuid.UUID,
    before: dict,
    after: dict,
) -> BlockEvent:
    """Emit a ``content_updated`` event with full before/after block snapshots."""
    return _emit(
        db,
        block_id=block_id,
        event_type="content_updated",
        before=before,
        after=after,
    )


def emit_state_changed(
    db: Session,
    block_id: uuid.UUID,
    before_state: str,
    after_state: str,
) -> BlockEvent:
    """Emit a ``state_changed`` event."""
    return _emit(
        db,
        block_id=block_id,
        event_type="state_changed",
        before={"state": before_state},
        after={"state": after_state},
    )
