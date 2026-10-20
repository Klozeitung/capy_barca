"""create database_settings table

Revision ID: y5b7a6w80025
Revises: x4a6z5v70024
Create Date: 2026-10-20

Settings that apply to a whole database rather than to one of its views. Only
the owner of the database and admins may change them.

Schema changes
--------------
database_settings
    database_id         UUID PRIMARY KEY, FK blocks.id ON DELETE CASCADE
        The database block the settings belong to. Purging the database
        removes its settings.
    default_entry_icon  TEXT NULL
        Icon given to every entry created in the database. NULL: none.
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()

Backfill
--------
None. A database without a row has the default settings.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y5b7a6w80025"
down_revision: Union[str, None] = "x4a6z5v70024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "database_settings",
        sa.Column("database_id", sa.Uuid(), nullable=False),
        sa.Column("default_entry_icon", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("database_id"),
        sa.ForeignKeyConstraint(["database_id"], ["blocks.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("database_settings")
