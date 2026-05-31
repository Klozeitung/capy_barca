"""create automations table

Revision ID: j0m2l1h30010
Revises: i9l1k0g20009
Create Date: 2026-05-18

Creates the ``automations`` table that backs the If-This-Then-That
automation engine.  Each row is scoped to a database block and carries
a JSON trigger descriptor plus a JSON array of actions.

The ``set_updated_at()`` PL/pgSQL function is reused from the existing
migration (0002) — it is already present in the database and does not
need to be recreated here.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j0m2l1h30010"
down_revision: Union[str, None] = "i9l1k0g20009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automations",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column(
            "database_id",
            sa.UUID(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("trigger", sa.JSON(), nullable=False),
        sa.Column(
            "actions",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_automations_database_id", "automations", ["database_id"])

    # Reuse the existing set_updated_at() trigger function from migration 0002.
    op.execute("""
        CREATE TRIGGER trg_automations_updated_at
        BEFORE UPDATE ON automations
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_automations_updated_at ON automations")
    op.drop_index("ix_automations_database_id", table_name="automations")
    op.drop_table("automations")
