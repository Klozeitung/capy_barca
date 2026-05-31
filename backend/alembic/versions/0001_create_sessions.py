"""create sessions table

Revision ID: a1f3c2e40001
Revises:
Create Date: 2026-03-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1f3c2e40001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("token", sa.String(64), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_table("sessions")
