"""migrate created_by and last_edited_by values to user_id format

Revision ID: i9l1k0g20009
Revises: h8k0j9f10008
Create Date: 2026-05-16

Before this migration, ``created_by`` and ``last_edited_by`` property values
stored the username as plain text: ``{"username": "capybarca"}``.

After the multi-user refactor, they store the user UUID instead:
``{"user_id": "<uuid>"}``.

This migration converts all existing rows that contain a ``username`` key
(but no ``user_id`` key) to point at the admin user.  Rows with a NULL
value are also updated.

SQL notes
---------
* ``CAST(:admin_id AS TEXT)`` instead of ``:admin_id::text`` – the double-
  colon cast conflicts with SQLAlchemy's named-parameter parser.
* ``jsonb_exists(value, 'key')`` instead of ``value ? 'key'`` – the ``?``
  operator conflicts with psycopg2's positional-parameter placeholder.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i9l1k0g20009"
down_revision: Union[str, None] = "h8k0j9f10008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USER_SCHEMA_TYPES = ("created_by", "last_edited_by")


def upgrade() -> None:
    conn = op.get_bind()

    row = conn.execute(
        sa.text(
            "SELECT id FROM users WHERE role = 'admin' AND is_active = TRUE "
            "ORDER BY created_at ASC LIMIT 1"
        )
    ).fetchone()

    if row is None:
        return  # fresh installation – nothing to migrate

    admin_id: str = str(row[0])

    for schema_type in _USER_SCHEMA_TYPES:
        conn.execute(
            sa.text(
                """
                UPDATE property_values
                SET value = jsonb_build_object('user_id', CAST(:admin_id AS TEXT))
                WHERE property_schema_id IN (
                    SELECT id FROM property_schemas WHERE type = :schema_type
                )
                AND (
                    value IS NULL
                    OR (
                        jsonb_exists(value, 'username')
                        AND NOT jsonb_exists(value, 'user_id')
                    )
                )
                """
            ),
            {"admin_id": admin_id, "schema_type": schema_type},
        )


def downgrade() -> None:
    pass  # not reversible without storing the original username
