import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import Boolean, Column, DateTime, Text

from app.database.database import Base


class User(Base):
    """Application user, stored in the database."""

    __tablename__ = "users"

    id = Column(sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    username = Column(Text, unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    role = Column(Text, nullable=False, server_default="member")
    is_active = Column(Boolean, nullable=False, server_default=sa.true())
    # Preferred display date format (one of the canonical display tokens, e.g.
    # "DD.MM.YYYY"). The backend stores and exchanges dates exclusively in
    # canonical ISO 8601; this token only governs how the frontend renders
    # them. Python-level default keeps SQLite test inserts valid; server_default
    # backfills existing rows on migration.
    date_format = Column(
        Text,
        nullable=False,
        default="DD.MM.YYYY",
        server_default="DD.MM.YYYY",
    )
    # Python-level default ensures SQLite tests never invoke now() (a PostgreSQL
    # function); server_default is kept for the DDL column definition only.
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=sa.text("now()"),
    )
