"""create database_views and database_view_selections

Revision ID: r8u0t9p10018
Revises: q7t9s8o00017
Create Date: 2026-10-17

Moves database views out of the generic block preference store into tables of
their own, so the backend knows what a view is: which filters and sorts it
applies, and how it is laid out. Until now a view was an opaque JSON array
under the preference key ``views``, which only the frontend could interpret.

This revision creates the tables only. Existing views stay in
block_preferences until the follow-up revision migrates them.

Schema changes
--------------
database_views
    id             UUID PRIMARY KEY
        Kept stable across the migration of existing views, because the
        stored selection of a placement refers to it.
    database_id    UUID NOT NULL, FK blocks ON DELETE CASCADE, indexed
        The database block the view belongs to. Views are shared by every
        user and every placement of that database.
    name           TEXT NOT NULL DEFAULT ''
        Empty means "not named yet"; the frontend shows its localized default
        name for it, so the backend never has to pick a language.
    view_type      TEXT NOT NULL DEFAULT 'table'
    position       DOUBLE PRECISION NOT NULL
        Order of the view tabs.
    description    TEXT NULL
    filter_groups  JSONB NOT NULL DEFAULT '[]'
    sorts          JSONB NOT NULL DEFAULT '[]'
        The two parts the backend executes. Validated on every write.
    settings       JSONB NOT NULL DEFAULT '{}'
        Presentation only: column order, widths and visibility, calendar
        options, grouping, aggregations and the like.
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()

database_view_selections
    placement_id   UUID PRIMARY KEY, FK blocks ON DELETE CASCADE
        The block that displays the database: the database block itself or a
        linked_database block. Each placement remembers its own view.
    view_id        UUID NOT NULL, FK database_views ON DELETE CASCADE
        Deleting a view deletes the selections pointing at it; the placement
        then falls back to the first view.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "r8u0t9p10018"
down_revision: Union[str, None] = "q7t9s8o00017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "database_views",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("database_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("view_type", sa.Text(), nullable=False, server_default="table"),
        sa.Column("position", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "filter_groups", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("sorts", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "settings", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["database_id"], ["blocks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_database_views_database_id", "database_views", ["database_id"]
    )

    op.create_table(
        "database_view_selections",
        sa.Column("placement_id", sa.Uuid(), nullable=False),
        sa.Column("view_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["placement_id"], ["blocks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["view_id"], ["database_views.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("placement_id"),
    )
    op.create_index(
        "ix_database_view_selections_view_id",
        "database_view_selections",
        ["view_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_database_view_selections_view_id", table_name="database_view_selections"
    )
    op.drop_table("database_view_selections")
    op.drop_index("ix_database_views_database_id", table_name="database_views")
    op.drop_table("database_views")
