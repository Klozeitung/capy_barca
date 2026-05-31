"""create comments table

Revision ID: k1n3m2i40011
Revises: j0m2l1h30010
Create Date: 2026-05-30

Creates the ``comments`` table that backs the per-block comment section
rendered beneath the property section in both MainView and SideView.

``author_id`` is nullable — no FK constraint to ``users`` so that comments
survive user deletion gracefully.  The application resolves display names
client-side via ``/api/users/names``.

The ``set_updated_at()`` PL/pgSQL trigger function is reused from
migration 0002 — it is already present and does not need to be recreated.

Uses raw SQL with IF NOT EXISTS throughout so that this migration is
idempotent on instances where the table was already created outside of
Alembic (e.g. via an earlier create_all() run).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "k1n3m2i40011"
down_revision: Union[str, None] = "j0m2l1h30010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id         UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            block_id   UUID NOT NULL REFERENCES blocks (id) ON DELETE CASCADE,
            author_id  UUID,
            text       TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_comments_block_id ON comments (block_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_comments_author_id ON comments (author_id)
    """)
    op.execute("""
        CREATE OR REPLACE TRIGGER trg_comments_updated_at
        BEFORE UPDATE ON comments
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_comments_updated_at ON comments")
    op.execute("DROP INDEX IF EXISTS ix_comments_author_id")
    op.execute("DROP INDEX IF EXISTS ix_comments_block_id")
    op.execute("DROP TABLE IF EXISTS comments")
