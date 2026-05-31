"""[no-op] database_templates table — superseded by entry_template block type

Revision ID: m3p5o4k60013
Revises: l2o4n3j50012
Create Date: 2026-05-30
Voided: 2026-05-31

Originally created the ``database_templates`` table (JSON-blob approach).
That implementation has been superseded by the ``entry_template`` block type,
which stores templates as real database entry blocks so that schema changes
are automatically reflected without any synchronisation logic.

Migration 0014 drops the table on instances where it was created.
This migration is kept as a no-op to preserve the revision chain.
"""
from typing import Sequence, Union

revision: str = "m3p5o4k60013"
down_revision: Union[str, None] = "l2o4n3j50012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
