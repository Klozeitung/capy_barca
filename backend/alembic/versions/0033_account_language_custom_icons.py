"""account language and custom icons

Revision ID: a3d5c4e60033
Revises: f2c4b3d50032
Create Date: 2026-11-02

Adds ``users.language``, the language an account uses the application in
(``de`` or ``en``). Every account starts without one, existing accounts
included: the web app asks for language and date format after the next login
and stores the answer.

Adds ``custom_icons``, the images admins upload for use as icons. An icon is
referenced as ``custom:<id>`` wherever an icon string is stored. The file lives
under ``static/uploads/icons/`` as ``stored_name``. Deleting the uploading
account keeps the icon and clears ``uploaded_by``.

Downgrade
---------
Drops the table and the column. Icon strings that reference a custom icon stay
in place and render as missing icons; their files are not removed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3d5c4e60033"
down_revision: Union[str, None] = "f2c4b3d50032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LANGUAGE_COLUMN = "language"
LANGUAGE_CHECK = "ck_users_language"
ICONS_TABLE = "custom_icons"


def upgrade() -> None:
    op.add_column("users", sa.Column(LANGUAGE_COLUMN, sa.Text(), nullable=True))
    op.create_check_constraint(
        LANGUAGE_CHECK, "users", "language IS NULL OR language IN ('de', 'en')"
    )
    op.create_table(
        ICONS_TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("stored_name", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "uploaded_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table(ICONS_TABLE)
    op.drop_constraint(LANGUAGE_CHECK, "users", type_="check")
    op.drop_column("users", LANGUAGE_COLUMN)
