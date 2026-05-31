"""add user_id to sessions

Revision ID: h8k0j9f10008
Revises: g7j9i8e00007
Create Date: 2026-05-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h8k0j9f10008"
down_revision: Union[str, None] = "g7j9i8e00007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable so pre-migration sessions (without an owner) remain valid
    # until they expire naturally. All new sessions carry a user_id.
    op.add_column(
        "sessions",
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_column("sessions", "user_id")
