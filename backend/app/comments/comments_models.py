"""
Comment model.

A Comment is a plain-text annotation attached to a block (typically a page
or a database entry).  Comments are ordered by creation time and are
displayed in the CommentSection beneath the PropertySection in both
MainView and SideView.

The ``author_id`` field stores the UUID of the user who created the comment,
resolved from the session token at write time via the shared deps layer.
It is nullable to gracefully handle legacy rows created before this field
existed; in practice all new rows will carry a value.
"""
import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base


class Comment(Base):
    """A single comment attached to a block."""

    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    block_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        sa.ForeignKey("blocks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable FK — no hard FK constraint to users so that comments survive
    # user deletion gracefully.  The application resolves names via
    # /api/users/names on the frontend.
    author_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        sa.UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
