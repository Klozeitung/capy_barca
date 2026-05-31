"""drop database_templates table

Revision ID: n4q6p5l70014
Revises: m3p5o4k60013
Create Date: 2026-05-31

Drops the ``database_templates`` table introduced in 0013.

That implementation stored templates as JSON blobs in a separate table.
It has been superseded by the ``entry_template`` block type, which stores
templates as real database entry blocks (type = 'entry_template') so that
schema changes (adding / removing properties) are automatically reflected
in all templates without any synchronisation logic.

``DROP TABLE IF EXISTS`` makes this migration safe to run on clean installs
that never had the table (0013 is effectively a no-op there after this).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "n4q6p5l70014"
down_revision: Union[str, None] = "m3p5o4k60013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_database_templates_updated_at ON database_templates"
    )
    op.execute("DROP INDEX IF EXISTS ix_database_templates_database_id")
    op.execute("DROP TABLE IF EXISTS database_templates")


def downgrade() -> None:
    # Intentionally not recreating the table on downgrade — the old
    # implementation is abandoned. Rolling back past this point requires
    # reverting to the 0013 schema manually.
    pass
