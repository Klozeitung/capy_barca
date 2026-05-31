"""create users table

Revision ID: g7j9i8e00007
Revises: f6i8h7d90006
Create Date: 2026-05-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g7j9i8e00007"
down_revision: Union[str, None] = "f6i8h7d90006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "role",
            sa.Text(),
            nullable=False,
            server_default="member",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
