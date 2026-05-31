"""add group column to property_schemas

Revision ID: e5h7g6c80005
Revises: d4g6f5b70004
Create Date: 2026-04-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5h7g6c80005"
down_revision: Union[str, None] = "d4g6f5b70004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOT NULL with server_default so existing rows are backfilled immediately.
    op.add_column(
        "property_schemas",
        sa.Column(
            "group",
            sa.Text(),
            nullable=False,
            server_default="Standard",
        ),
    )


def downgrade() -> None:
    op.drop_column("property_schemas", "group")
