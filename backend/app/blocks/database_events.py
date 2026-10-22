"""
Change events of the database API.

The database routers announce committed changes to the clients of a database
through these helpers, always after ``db.commit()`` so a client that reacts
fetches committed data. Every event carries the database id as ``block_id``,
which is how open views of that database pick it up.

database_schema_updated   – the property schemas of the database changed
                            (a mirror property was created).
database_entries_updated  – entries of the database changed: their values,
                            or which entries it holds and in what order.
created                   – a new entry; carries the block descriptor.

Routes that add, move, reorder or restore blocks call
:func:`emit_listing_changes`, which announces every database whose list of
entries the request changed, so views of it can load what they show again.
"""
import uuid
from typing import Iterable, Union

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blocks import entry_changes
from app.blocks.models import Block
from app.ws.broadcaster import emit_block_event

DatabaseId = Union[uuid.UUID, str]


def emit_schema_updated(db: Session, database_id: DatabaseId) -> None:
    """Announce that the property schemas of *database_id* changed."""
    emit_block_event(
        db=db,
        event_type="database_schema_updated",
        block_id=str(database_id),
        before=None,
        after={"database_id": str(database_id)},
    )


def emit_entries_updated(db: Session, database_id: DatabaseId) -> None:
    """Announce that entry values of *database_id* changed."""
    emit_block_event(
        db=db,
        event_type="database_entries_updated",
        block_id=str(database_id),
        before=None,
        after={"database_id": str(database_id)},
    )


def emit_entries_updated_for(db: Session, database_ids: Iterable[DatabaseId]) -> None:
    """:func:`emit_entries_updated` for each of *database_ids*, in order."""
    for database_id in database_ids:
        emit_entries_updated(db, database_id)


def emit_listing_changes(
    db: Session,
    *,
    announced: Iterable[DatabaseId] = (),
) -> list[uuid.UUID]:
    """
    Announce every database whose list of entries *db* changed.

    The list changes when an entry is created, deleted, moved in or out,
    reordered, trashed or restored; ``app.blocks.entry_changes`` records the
    parents concerned during the flush. Of those, the databases are announced
    with ``database_entries_updated``, which then carries ``listing``.
    Databases in *announced* are skipped, for callers that already sent the
    event for them in the same request. Call after ``db.commit()``.

    Returns the ids of the databases announced, in the order they were sent.
    """
    skip = {str(database_id) for database_id in announced}
    parents = [p for p in entry_changes.changed_listings(db) if str(p) not in skip]
    if not parents:
        return []
    databases = sorted(
        db.scalars(
            select(Block.id).where(Block.id.in_(parents), Block.type == "database")
        ).all(),
        key=str,
    )
    for database_id in databases:
        emit_entries_updated(db, database_id)
    return databases


def emit_entry_created(db: Session, database_id: uuid.UUID, block) -> None:
    """Announce the new entry *block* of *database_id*."""
    emit_block_event(
        db=db,
        event_type="created",
        block_id=str(block.id),
        before=None,
        after={
            "id": str(block.id),
            "parent_id": str(database_id),
            "type": "page",
            "position": block.position,
            "state": block.state,
            "icon": block.icon,
        },
    )
