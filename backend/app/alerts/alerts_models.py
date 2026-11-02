"""
Alert model.

An alert is a personal reminder: one account asks to be notified about one
entry at one point in time. Alerts appear in the property of type ``alerts``
of the entry's database, of which each database has at most one.

Single source of truth
----------------------
This table is the only place alerts are stored. ``property_values`` never
holds a value for an ``alerts`` property; the entry endpoints read the
caller's own alerts from here and write them into the response (see
``app.alerts.alerts_repository.values_for_entries``). Other accounts' alerts
are therefore never part of a response, and no write can overwrite them.

Lifecycle
---------
``fire_at`` is an absolute instant. The scheduler claims a due alert by
setting ``fired_at``; ``delivered`` records whether the notification actually
went out, which it does not when the alert was more than the grace period
overdue, its entry was in the trash or the account had lost read access.
``dismissed_at`` is set when the account closes the notification.

Every foreign key cascades: purging the entry, deleting the property or the
account removes the alerts that depend on it.
"""
import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base

# Property type whose cells show alerts.
ALERTS_PROPERTY_TYPE = "alerts"

# Longest title an alert may carry.
MAX_TITLE_LENGTH = 200

# Most alerts one account may keep on one entry.
MAX_ALERTS_PER_ENTRY = 50


class Alert(Base):
    """One reminder of one account for one entry."""

    __tablename__ = "alerts"
    __table_args__ = (
        # The scheduler looks for alerts not yet fired, in time order.
        sa.Index("ix_alerts_due", "fired_at", "fire_at"),
        # The entry endpoints look up the caller's alerts for a set of entries.
        sa.Index("ix_alerts_user_block", "user_id", "block_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid.uuid4
    )
    block_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("blocks.id", ondelete="CASCADE"),
        nullable=False,
    )
    schema_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("property_schemas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    fire_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    title: Mapped[str] = mapped_column(
        sa.Text, nullable=False, default="", server_default=""
    )
    fired_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True, default=None
    )
    delivered: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
