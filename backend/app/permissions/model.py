"""
ORM models for the block permission layer.

BlockPermission
    One optional row per block that has an *explicit* permission setting.
    Absence of a row means the block inherits from its parent.

BlockPermissionGrant
    Each row whitelists one user for a specific block when the block's
    permission mode is 'whitelist'.  ``user_id`` carries no FK to users so
    that grants are not silently removed when a user account is deactivated.
"""
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


class BlockPermission(Base):
    """
    Stores the explicit permission setting for a single block.

    ``mode`` valid values
    ---------------------
    'everyone'   All authenticated users may read this block.
    'private'    Only the block owner may read this block.
    'whitelist'  The block owner and all entries in
                 ``block_permission_grants`` may read this block.

    A missing row is semantically equivalent to mode='inherit': the
    effective permission is resolved by walking up the parent chain until
    a row is found.  The workspace root always has an explicit row
    (mode='everyone') inserted by migration 0015 to anchor the walk.
    """

    __tablename__ = "block_permissions"

    block_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("blocks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mode: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default="everyone",
        server_default="everyone",
    )

    grants: Mapped[list["BlockPermissionGrant"]] = relationship(
        "BlockPermissionGrant",
        back_populates="permission",
        cascade="all, delete-orphan",
        lazy="select",
    )


class BlockPermissionGrant(Base):
    """
    Whitelist entry: grants one user read access to a specific block.

    ``user_id`` intentionally has no FK constraint to the users table so
    that grants survive user deactivation without requiring a trigger or
    application-level cleanup.  The application filters out deactivated
    users when displaying the grant list.
    """

    __tablename__ = "block_permission_grants"
    __table_args__ = (
        sa.UniqueConstraint(
            "block_id", "user_id", name="uq_block_permission_grants"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid.uuid4
    )
    block_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("block_permissions.block_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)

    permission: Mapped["BlockPermission"] = relationship(
        "BlockPermission",
        back_populates="grants",
        lazy="select",
    )
