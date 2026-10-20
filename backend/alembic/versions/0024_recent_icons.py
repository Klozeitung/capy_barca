"""add recent_icons to users

Revision ID: x4a6z5v70024
Revises: w3z5y4u60023
Create Date: 2026-10-20

Remembers the icons each user picked most recently, so the icon picker can
offer them first.

Schema changes
--------------
users
    recent_icons JSONB NOT NULL DEFAULT '[]'
        At most ten entries ``{"icon": str, "used_at": ISO 8601}``, newest
        first. Maintained by ``app.users.repository.record_icon_use``.

Backfill
--------
Existing rows receive the empty list through the server default.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "x4a6z5v70024"
down_revision: Union[str, None] = "w3z5y4u60023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "recent_icons",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "recent_icons")
