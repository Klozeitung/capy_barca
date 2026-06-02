"""add block ownership and permissions tables

Revision ID: o5r7q6m80015
Revises: n4q6p5l70014
Create Date: 2026-06-02

Adds a fine-grained permission layer on top of the existing role system.

Schema changes
--------------
blocks
    owner_id UUID NULL
        UUID of the user who owns this block.  No FK constraint so that
        deleting a user account does not affect the block tree.
        Backfilled to the first active admin for all existing rows.

block_permissions
    block_id UUID PK FK blocks.id ON DELETE CASCADE
    mode     TEXT NOT NULL DEFAULT 'everyone'
        Permission mode for this block.  Valid values:
          'everyone'  – all authenticated users may read
          'inherit'   – not stored; absence of a row means inherit
          'private'   – owner only
          'whitelist' – owner + explicitly granted users

        Note: a missing row is treated as 'inherit' by the application.
        This table stores *overrides* only.

block_permission_grants
    id       UUID PK
    block_id UUID FK block_permissions.block_id ON DELETE CASCADE
    user_id  UUID NOT NULL  (no FK — grants survive user deactivation)
    UNIQUE(block_id, user_id)

Data migration
--------------
1. All existing blocks receive owner_id = first active admin user.
   Blocks that already have owner_id populated are left untouched.
2. The workspace root block (00000000-0000-0000-0000-000000000001) receives
   an explicit block_permissions row with mode='everyone' so that the root
   of the tree always has a reachable non-inherit anchor for the inheritance
   walk, preventing infinite recursion on edge-case orphaned blocks.
"""
from typing import Sequence, Union
import uuid as _uuid

import sqlalchemy as sa
from alembic import op

revision: str = "o5r7q6m80015"
down_revision: Union[str, None] = "n4q6p5l70014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WORKSPACE_ROOT_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    conn = op.get_bind()

    # ── blocks: add owner_id ──────────────────────────────────────────────────
    op.add_column(
        "blocks",
        sa.Column("owner_id", sa.UUID(), nullable=True),
    )
    op.create_index("ix_blocks_owner_id", "blocks", ["owner_id"])

    # ── block_permissions ─────────────────────────────────────────────────────
    op.create_table(
        "block_permissions",
        sa.Column("block_id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column(
            "mode",
            sa.Text(),
            nullable=False,
            server_default="everyone",
        ),
        sa.ForeignKeyConstraint(
            ["block_id"], ["blocks.id"], ondelete="CASCADE"
        ),
    )

    # ── block_permission_grants ───────────────────────────────────────────────
    op.create_table(
        "block_permission_grants",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("block_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["block_id"], ["block_permissions.block_id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "block_id", "user_id", name="uq_block_permission_grants"
        ),
    )
    op.create_index(
        "ix_block_permission_grants_block_id",
        "block_permission_grants",
        ["block_id"],
    )

    # ── Backfill: set owner_id to first active admin for all blocks ───────────
    row = conn.execute(
        sa.text(
            "SELECT id FROM users WHERE role = 'admin' AND is_active = TRUE "
            "ORDER BY created_at ASC LIMIT 1"
        )
    ).fetchone()

    if row is not None:
        admin_id: str = str(row[0])
        conn.execute(
            sa.text(
                "UPDATE blocks SET owner_id = CAST(:admin_id AS UUID) "
                "WHERE owner_id IS NULL"
            ),
            {"admin_id": admin_id},
        )

    # ── Seed workspace root with mode='everyone' ──────────────────────────────
    # This ensures the inheritance walk always finds an anchor at the tree root
    # and never falls off the end of the parent chain without resolution.
    conn.execute(
        sa.text(
            "INSERT INTO block_permissions (block_id, mode) "
            "VALUES (CAST(:root_id AS UUID), 'everyone') "
            "ON CONFLICT (block_id) DO NOTHING"
        ),
        {"root_id": _WORKSPACE_ROOT_ID},
    )

    # ── Seed first-level blocks with mode='private' ───────────────────────────
    # Direct children of the workspace root are private by default so that
    # top-level content is not exposed to all users unless explicitly shared.
    # Blocks at deeper levels receive no row (inherit from their first-level
    # ancestor).  The workspace root itself is explicitly excluded.
    conn.execute(
        sa.text(
            "INSERT INTO block_permissions (block_id, mode) "
            "SELECT b.id, 'private' FROM blocks b "
            "WHERE b.parent_id = CAST(:root_id AS UUID) "
            "AND b.id != CAST(:root_id AS UUID) "
            "ON CONFLICT (block_id) DO NOTHING"
        ),
        {"root_id": _WORKSPACE_ROOT_ID},
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM block_permissions WHERE block_id = "
        f"'{_WORKSPACE_ROOT_ID}'"
    )
    op.drop_index(
        "ix_block_permission_grants_block_id",
        table_name="block_permission_grants",
    )
    op.drop_table("block_permission_grants")
    op.drop_table("block_permissions")
    op.drop_index("ix_blocks_owner_id", table_name="blocks")
    op.drop_column("blocks", "owner_id")
