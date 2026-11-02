"""calendar alerts and account time zones

Revision ID: f2c4b3d50032
Revises: e1b3a2c40031
Create Date: 2026-11-02

Adds ``alerts.kind``: ``user`` for the reminders people set themselves,
``system`` for the one the application keeps for every calendar date. Every
existing alert is a user alert. A partial unique index allows one unfired
system alert per entry, date property and recipient; fired ones stay until
they are dismissed and cleaned up.

Adds ``users.timezone``, the IANA zone an account works in. Existing accounts
start with ``Europe/Berlin``; the web app replaces it with the browser's zone
after the next login. New accounts start without one and use the
installation's zone until then.

Times of day already stored are not touched here. They keep being read as
local time until ``python -m app.maintenance.convert_dates_to_utc`` has
stored them in UTC (see app.timezones).

Downgrade
---------
Drops the system alerts, the column ``kind`` with its index and the column
``timezone``.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2c4b3d50032"
down_revision: Union[str, None] = "e1b3a2c40031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KIND_COLUMN = "kind"
KIND_CHECK = "ck_alerts_kind"
SINGLE_SYSTEM_INDEX = "uq_alerts_single_system"
TIMEZONE_COLUMN = "timezone"
EXISTING_ACCOUNTS_ZONE = "Europe/Berlin"


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column(KIND_COLUMN, sa.Text(), nullable=False, server_default="user"),
    )
    op.create_check_constraint(KIND_CHECK, "alerts", "kind IN ('user', 'system')")
    op.create_index(
        SINGLE_SYSTEM_INDEX,
        "alerts",
        ["block_id", "schema_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'system' AND fired_at IS NULL"),
    )
    op.add_column("users", sa.Column(TIMEZONE_COLUMN, sa.Text(), nullable=True))
    op.execute(
        sa.text("UPDATE users SET timezone = :zone").bindparams(zone=EXISTING_ACCOUNTS_ZONE)
    )


def downgrade() -> None:
    op.drop_column("users", TIMEZONE_COLUMN)
    op.execute("DELETE FROM alerts WHERE kind = 'system'")
    op.drop_index(SINGLE_SYSTEM_INDEX, table_name="alerts")
    op.drop_constraint(KIND_CHECK, "alerts", type_="check")
    op.drop_column("alerts", KIND_COLUMN)
