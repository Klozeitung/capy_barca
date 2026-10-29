"""installation-wide API settings and the print permission

Revision ID: d0a2f1b30030
Revises: c9f1e0a20029
Create Date: 2026-10-29

Creates ``app_settings``, a key-value table for settings that belong to the
installation rather than to an account: the connection to a local AI service
and the custom printer that Quick Print sends to. Each row holds one setting
as a JSON object under a fixed key. Secret values inside it (keys, header
values) are stored encrypted by the application; this revision does not know
or touch them.

Adds ``users.can_print``. Only accounts with this flag, and admins, may send
to the custom printer. Every existing account starts without it.

Downgrade
---------
Drops the column and the table, and with it every stored setting.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d0a2f1b30030"
down_revision: Union[str, None] = "c9f1e0a20029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "app_settings"
COLUMN = "can_print"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("key", sa.Text(), primary_key=True, nullable=False),
        sa.Column(
            "value",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", COLUMN)
    op.drop_table(TABLE)
