"""alerts: personal reminders on database entries

Revision ID: e1b3a2c40031
Revises: d0a2f1b30030
Create Date: 2026-11-02

Creates ``alerts``, the only storage of the reminders shown in properties of
type ``alerts``. Each row belongs to one account, one entry and one property;
all three foreign keys cascade, so purging an entry, deleting the property or
deleting the account removes the alerts that depend on it.

Adds a partial unique index on ``property_schemas`` so a database has at most
one property of type ``alerts``. The API checks this before it writes; the
index decides between two requests that pass the check at the same time.

Two indexes serve the two readers: ``ix_alerts_due`` the scheduler, which
looks for alerts not yet fired in time order, and ``ix_alerts_user_block`` the
entry endpoints, which look up the caller's alerts for a set of entries.

Downgrade
---------
Drops the table and every alert in it, and the index on ``property_schemas``.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1b3a2c40031"
down_revision: Union[str, None] = "d0a2f1b30030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "alerts"
SINGLE_ALERTS_INDEX = "uq_property_schemas_single_alerts"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "block_id",
            sa.Uuid(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "schema_id",
            sa.Uuid(),
            sa.ForeignKey("property_schemas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_alerts_due", TABLE, ["fired_at", "fire_at"])
    op.create_index("ix_alerts_user_block", TABLE, ["user_id", "block_id"])
    op.create_index("ix_alerts_schema_id", TABLE, ["schema_id"])
    op.create_index(
        SINGLE_ALERTS_INDEX,
        "property_schemas",
        ["database_id"],
        unique=True,
        postgresql_where=sa.text("type = 'alerts'"),
    )


def downgrade() -> None:
    op.drop_index(SINGLE_ALERTS_INDEX, table_name="property_schemas")
    op.drop_index("ix_alerts_schema_id", table_name=TABLE)
    op.drop_index("ix_alerts_user_block", table_name=TABLE)
    op.drop_index("ix_alerts_due", table_name=TABLE)
    op.drop_table(TABLE)
