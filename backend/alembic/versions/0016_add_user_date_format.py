"""add date_format preference to users

Revision ID: p6s8r7n90016
Revises: o5r7q6m80015
Create Date: 2026-06-15

Adds a per-user display date-format preference.

Schema changes
--------------
users
    date_format TEXT NOT NULL DEFAULT 'DD.MM.YYYY'
        The user's preferred display date format token. Governs frontend
        rendering only; dates are always stored and exchanged as ISO 8601.
        NOT NULL with a server_default so all existing rows are backfilled
        to the European default immediately.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "p6s8r7n90016"
down_revision: Union[str, None] = "o5r7q6m80015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "date_format",
            sa.Text(),
            nullable=False,
            server_default="DD.MM.YYYY",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "date_format")
