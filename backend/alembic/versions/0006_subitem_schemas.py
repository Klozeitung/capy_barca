"""add parent_item / sub_item system schema pair (data migration via seed endpoint)

Revision ID: f6i8h7d90006
Revises: e5h7g6c80005
Create Date: 2026-05-08

No database-schema changes are required for sub-items.  The new
``parent_item`` and ``sub_item`` property types are stored in the existing
``property_schemas`` and ``property_values`` tables using the same JSONB
structure that relation properties already use.

The schemas themselves are seeded idempotently via the
``POST /api/databases/{id}/seed-readonly-schemas`` endpoint, which is called
by the frontend on every DatabaseBlock mount.  Existing databases receive the
new pair automatically on next open without any data loss.

This migration therefore serves as a documentation checkpoint only.  It does
not execute any SQL statements.
"""
from typing import Sequence, Union

revision: str = "f6i8h7d90006"
down_revision: Union[str, None] = "e5h7g6c80005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass  # Data seeded via seed_readonly_schemas endpoint at runtime.


def downgrade() -> None:
    pass  # No schema to roll back; remove via seed endpoint or direct SQL if needed.
