"""
ORM model for uploaded icons.

An admin uploads an image; every account may then pick it wherever icons are
picked. The icon is referenced as ``custom:<id>`` (``app.icons.names``) and its
file lives under ``static/uploads/icons/{stored_name}``.
"""
import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base


class CustomIcon(Base):
    """
    One uploaded icon.

    ``name`` is what the icon picker shows and searches; it comes from the
    file name at upload. ``uploaded_by`` is cleared when the account is
    deleted, the icon stays.
    """

    __tablename__ = "custom_icons"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    stored_name: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
