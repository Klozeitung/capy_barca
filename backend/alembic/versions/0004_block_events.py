"""create block_events table for full mutation history

Revision ID: d4g6f5b70004
Revises: c3f5e4a60003
Create Date: 2026-03-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4g6f5b70004"
down_revision: Union[str, None] = "c3f5e4a60003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # block_id is nullable so that history is retained even after a block is
    # hard-deleted (purged). The FK is therefore not enforced with CASCADE;
    # instead, block_id is set to NULL on delete, and event_type + before/after
    # snapshots still hold the full audit trail.
    op.create_table(
        "block_events",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column(
            "block_id",
            sa.UUID(),
            sa.ForeignKey("blocks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_block_events_block_id", "block_events", ["block_id"])
    op.create_index("ix_block_events_created_at", "block_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_block_events_created_at", table_name="block_events")
    op.drop_index("ix_block_events_block_id", table_name="block_events")
    op.drop_table("block_events")
