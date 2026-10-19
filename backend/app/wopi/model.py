"""
ORM model for the registry of issued WOPI tokens.

WopiToken
    One row per access token the token endpoint handed out.

Why this exists
---------------
A WOPI token is self-contained and HMAC-signed, so on its own the server
cannot tell a token it issued from one signed by anyone else holding
``SECRET_KEY``, and it cannot withdraw a token before it expires. Every file
endpoint therefore also requires the token's ``jti`` claim to name a row here
that matches the token's account, block and file. A token whose row is gone
stops working at once.

Rows are deleted rather than flagged: by the database together with the
session they were issued in (logout, session expiry), the account or the
block they belong to; on a password change for every token of that account;
and when they expire (on every issuance and at startup). The table therefore
holds only tokens that are currently valid and does not grow with the age of
the installation.

Tying a token to its session means logging out on one device ends the editor
sessions opened there and leaves those on other devices alone, which would
otherwise lose their next save.
"""
import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base


class WopiToken(Base):
    """A WOPI access token that was issued and has not been withdrawn."""

    __tablename__ = "wopi_tokens"

    jti: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    block_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("blocks.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_uuid: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    # Key of the session the token was issued in. Nullable for a request that
    # carried no stored session, which only happens with overridden
    # authentication in tests; such a token ends only by the other paths.
    session_key: Mapped[Optional[str]] = mapped_column(
        sa.String(64),
        sa.ForeignKey("sessions.token", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    issued_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, index=True,
    )
