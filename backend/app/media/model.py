"""
ORM model for the media-file to block mapping.

MediaFile
    One row per uploaded file in a flat storage category, recording which
    block it belongs to.

Why this exists
---------------
Media and file uploads land in one directory per category:

    static/uploads/media/{image|video|audio|pdf}/{file_uuid}{ext}
    static/uploads/files/{file_uuid}{ext}

Nothing in that layout says which block a file belongs to, so the block check
on the media endpoints could only govern *where* a file was written. A caller
who knew a ``file_uuid`` could address it through any block id they could
reach, including to delete it. This table is what makes that question
answerable.

Drive files are deliberately absent. They live under
``static/uploads/drives/{block_id}/``, so the directory is already the mapping
and the block check governs them completely. Recording them here would add a
second source of truth that has to be kept in step through every drive move,
for no gain.
"""
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base


class MediaFile(Base):
    """
    Maps one stored file to the block that owns it.

    ``block_id`` intentionally carries no FK constraint to the blocks table,
    for the same reason ``BlockPermissionGrant.user_id`` carries none to users:
    the row is a record of where a file came from, not a live reference. A
    constraint would also mean an upload naming a block that does not exist
    fails with an integrity error deep in the request, where the endpoint
    currently answers on its own terms.

    A row whose block has since been purged is harmless: block ids are never
    reused, so it can only ever match the request it was written for, and the
    file it names is gone from disk anyway.
    """

    __tablename__ = "media_files"

    file_uuid: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True)
    block_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False, index=True)
    category: Mapped[str] = mapped_column(sa.Text, nullable=False)
    stored_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
