"""separate reading from writing in block permissions

Revision ID: u1x3w2s40021
Revises: t0w2v1r30020
Create Date: 2026-10-18

Until now every grant of access to a block was full access: whoever could see
a block could also change, move and delete it. This revision adds an access
level to the two places that hand out access to accounts other than the owner.

block_permissions.everyone_level  TEXT NOT NULL DEFAULT 'write'
    The level every account receives while the block's mode is 'everyone'.
    Ignored for the other modes.

block_permission_grants.level  TEXT NOT NULL DEFAULT 'write'
    The level of one whitelisted account.

Both columns accept 'read' or 'write' only (check constraints). The owner of
a block and every admin always have write access; their access is not stored.

Existing rows receive 'write', so every account keeps exactly the access it
had before the upgrade.

Downgrade
---------
Drops both columns. Read-only grants become full grants again, which is the
behaviour of the previous revision.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u1x3w2s40021"
down_revision: Union[str, None] = "t0w2v1r30020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEVELS = ("read", "write")
_LEVEL_CHECK = "{column} IN ('read', 'write')"


def upgrade() -> None:
    op.add_column(
        "block_permissions",
        sa.Column("everyone_level", sa.Text(), nullable=False, server_default="write"),
    )
    op.create_check_constraint(
        "ck_block_permissions_everyone_level",
        "block_permissions",
        _LEVEL_CHECK.format(column="everyone_level"),
    )

    op.add_column(
        "block_permission_grants",
        sa.Column("level", sa.Text(), nullable=False, server_default="write"),
    )
    op.create_check_constraint(
        "ck_block_permission_grants_level",
        "block_permission_grants",
        _LEVEL_CHECK.format(column="level"),
    )


def downgrade() -> None:
    op.drop_constraint("ck_block_permission_grants_level", "block_permission_grants", type_="check")
    op.drop_column("block_permission_grants", "level")
    op.drop_constraint("ck_block_permissions_everyone_level", "block_permissions", type_="check")
    op.drop_column("block_permissions", "everyone_level")
