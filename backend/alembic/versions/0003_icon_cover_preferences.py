"""add icon and cover to blocks; create block_preferences table

Revision ID: c3f5e4a60003
Revises: b2e4d3f50002
Create Date: 2026-03-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3f5e4a60003"
down_revision: Union[str, None] = "b2e4d3f50002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── blocks: icon + cover columns ─────────────────────────────────────────
    op.add_column("blocks", sa.Column("icon", sa.Text(), nullable=True))
    op.add_column("blocks", sa.Column("cover", sa.Text(), nullable=True))

    # ── block_preferences ────────────────────────────────────────────────────
    # Intentionally user-id-less for now (single-user system).
    # Upgrade path: add user_id column + update unique constraint in a future
    # migration when multi-user support is introduced.
    op.create_table(
        "block_preferences",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column(
            "block_id",
            sa.UUID(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.UniqueConstraint("block_id", "key", name="uq_block_preferences_block_key"),
    )
    op.create_index("ix_block_preferences_block_id", "block_preferences", ["block_id"])


def downgrade() -> None:
    op.drop_index("ix_block_preferences_block_id", table_name="block_preferences")
    op.drop_table("block_preferences")
    op.drop_column("blocks", "cover")
    op.drop_column("blocks", "icon")
