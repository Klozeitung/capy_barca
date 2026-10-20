"""
Change events of the database API.

The database routers announce committed changes to the clients of a database
through these helpers, always after ``db.commit()`` so a client that reacts
fetches committed data. Every event carries the database id as ``block_id``,
which is how open views of that database pick it up.

database_schema_updated   – the property schemas of the database changed
                            (a mirror property was created).
database_entries_updated  – entry values of the database changed.
created                   – a new entry; carries the block descriptor.
"""
import uuid
from typing import Iterable, Union

from sqlalchemy.orm import Session

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
