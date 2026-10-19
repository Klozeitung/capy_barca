"""
ORM models for the block permission layer.

BlockPermission
    One optional row per block that has an *explicit* permission setting.
    Absence of a row means the block inherits from its parent.

BlockPermissionGrant
    Each row whitelists one user for a specific block when the block's
    permission mode is 'whitelist'.  ``user_id`` carries no FK to users so
    that grants are not silently removed when a user account is deactivated.

Access levels
-------------
Access is either ``read`` or ``write``. Under a single row, the owner of the
block carrying it always has write access; everybody else gets the level
stored with the permission that admits them: ``everyone_level`` for mode
'everyone', the grant's ``level`` for a whitelist entry. Across the chain the
lowest of these levels applies. Admins have write access everywhere.
"""
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base

READ = "read"
WRITE = "write"

#: Access levels, from least to most access.
LEVELS: tuple[str, ...] = (READ, WRITE)


class BlockPermission(Base):
    """
    Stores the explicit permission setting for a single block.

    ``mode`` valid values
    ---------------------
    'everyone'   All authenticated users have access, at ``everyone_level``.
    'private'    Only the block owner has access.
    'whitelist'  The block owner and all entries in
                 ``block_permission_grants`` have access, each at the level
                 of its grant.

    A missing row is semantically equivalent to mode='inherit'. Restrictions
    accumulate: an account reaches a block only if every explicit row on the
    chain from the block up to the workspace root admits it, at the lowest
    level any of them grants. A row can therefore narrow access for its
    subtree but never widen it. The workspace root always has an explicit row
    (mode='everyone') inserted by migration 0015 to anchor the walk.
    """

    __tablename__ = "block_permissions"
    __table_args__ = (
        sa.CheckConstraint(
            "everyone_level IN ('read', 'write')",
            name="ck_block_permissions_everyone_level",
        ),
    )

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
    everyone_level: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default=WRITE,
        server_default=WRITE,
    )

    grants: Mapped[list["BlockPermissionGrant"]] = relationship(
        "BlockPermissionGrant",
        back_populates="permission",
        cascade="all, delete-orphan",
        lazy="select",
    )


class BlockPermissionGrant(Base):
    """
    Whitelist entry: grants one user access to a specific block, at ``level``.

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
        sa.CheckConstraint(
            "level IN ('read', 'write')",
            name="ck_block_permission_grants_level",
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
    level: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default=WRITE,
        server_default=WRITE,
    )

    permission: Mapped["BlockPermission"] = relationship(
        "BlockPermission",
        back_populates="grants",
        lazy="select",
    )
