"""
Automation model.

An Automation describes a single If-This-Then-That rule, scoped to a
database block.  The trigger field is a JSON object that the engine matches
against incoming events; the actions field is a JSON array of API calls to
execute when the trigger fires.

Trigger JSON structure
----------------------
{
    "action_type":    "PropertyUpdate",
    "origin":         "user",
    "actor_uuid":     "",
    "db_uuid":        "<uuid>",
    "property_uuid":  "<uuid>",
    "old_value":      "",
    "new_value":      ""
}

Field matching rules (applied by the engine, not enforced here):
    ""          -> wildcard; matches any event value.
    "!<value>"  -> negation; matches when the event value differs from <value>.
    "<value>"   -> exact match.

Action JSON structure (one element of the actions array)
---------------------------------------------------------
{
    "endpoint": "PUT /api/databases/{trigger.db_uuid}/entries/{trigger.entry_id}/values/{trigger.property_uuid}",
    "body":     {"value": {"option": "Done"}}
}

Template variables resolved at execution time
---------------------------------------------
{trigger.entry_id}       UUID of the entry that fired the trigger
{trigger.db_uuid}        database UUID from the event
{trigger.property_uuid}  property schema UUID from the event
{trigger.new_value}      new cell value from the event
{today()}                current date as YYYY-MM-DD
"""
import uuid
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base


class Automation(Base):
    """
    A single automation rule owned by a database block.

    ``database_id`` determines which database this automation is scoped to.
    The SQL pre-filter in the engine uses this column to narrow candidates
    before the fine-grained Python matcher evaluates the full trigger JSON.

    ``trigger``  – JSON object with the seven event-matching fields.
    ``actions``  – JSON array of endpoint+body action descriptors.
    ``enabled``  – soft toggle; disabled automations are skipped entirely.
    """

    __tablename__ = "automations"
    __table_args__ = (sa.Index("ix_automations_database_id", "database_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid.uuid4
    )
    database_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("blocks.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true()
    )
    trigger: Mapped[Any] = mapped_column(sa.JSON, nullable=False)
    actions: Mapped[Any] = mapped_column(
        sa.JSON, nullable=False, default=list, server_default="[]"
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
