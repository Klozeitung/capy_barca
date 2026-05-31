"""add author_id to comments

Revision ID: l2o4n3j50012
Revises: k1n3m2i40011
Create Date: 2026-05-30

Adds the ``author_id`` column to the ``comments`` table.  The column was
introduced after the initial table creation (0011) and must be applied as
a separate migration on instances where the table already exists without it.

``author_id`` is nullable — no FK constraint to ``users`` so that comments
survive user deletion gracefully.  The application resolves display names
client-side via ``/api/users/names``.

Both statements use IF NOT EXISTS / IF NOT EXISTS so this migration is
idempotent regardless of whether 0011 already included the column.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "l2o4n3j50012"
down_revision: Union[str, None] = "k1n3m2i40011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE comments
        ADD COLUMN IF NOT EXISTS author_id UUID
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_comments_author_id ON comments (author_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_comments_author_id")
    op.execute("ALTER TABLE comments DROP COLUMN IF EXISTS author_id")
