"""create blocks, property_schemas, property_values; add triggers and workspace seed

Revision ID: b2e4d3f50002
Revises: a1f3c2e40001
Create Date: 2026-03-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2e4d3f50002"
down_revision: Union[str, None] = "a1f3c2e40001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Stable UUID for the workspace root block. Defined here as a plain string to
# keep the migration self-contained; the same value is exported from
# app.blocks.models.WORKSPACE_ROOT_ID for application code.
_WORKSPACE_ROOT_ID = "00000000-0000-0000-0000-000000000001"

# Tables that receive the updated_at trigger.
_TRIGGER_TABLES = ("blocks", "property_schemas", "property_values")


def upgrade() -> None:
    # ── blocks ────────────────────────────────────────────────────────────────
    op.create_table(
        "blocks",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column(
            "parent_id",
            sa.UUID(),
            sa.ForeignKey("blocks.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "reference_id",
            sa.UUID(),
            sa.ForeignKey("blocks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("position", sa.Float(), nullable=False),
        sa.Column(
            "state",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("content", postgresql.JSONB(), nullable=True),
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
    op.create_index("ix_blocks_parent_id", "blocks", ["parent_id"])
    op.create_index("ix_blocks_state", "blocks", ["state"])
    op.create_index("ix_blocks_type", "blocks", ["type"])

    # ── property_schemas ──────────────────────────────────────────────────────
    op.create_table(
        "property_schemas",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column(
            "database_id",
            sa.UUID(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=True),
        sa.Column("position", sa.Float(), nullable=False),
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
        sa.UniqueConstraint(
            "database_id", "name", name="uq_property_schemas_database_name"
        ),
    )
    op.create_index(
        "ix_property_schemas_database_id", "property_schemas", ["database_id"]
    )

    # ── property_values ───────────────────────────────────────────────────────
    op.create_table(
        "property_values",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column(
            "page_id",
            sa.UUID(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "property_schema_id",
            sa.UUID(),
            sa.ForeignKey("property_schemas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value", postgresql.JSONB(), nullable=True),
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
        sa.UniqueConstraint(
            "page_id", "property_schema_id", name="uq_property_values_page_schema"
        ),
    )
    op.create_index("ix_property_values_page_id", "property_values", ["page_id"])

    # ── updated_at trigger ────────────────────────────────────────────────────
    # A single shared function; each table gets its own trigger that calls it.
    op.execute("""
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    for table in _TRIGGER_TABLES:
        op.execute(f"""
            CREATE TRIGGER trg_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """)

    # ── workspace seed ────────────────────────────────────────────────────────
    # The workspace root is the single top-level ancestor for all content.
    # It is inserted here so the invariant "every non-root block has a parent"
    # holds from the first application boot.
    op.execute(f"""
        INSERT INTO blocks (id, parent_id, reference_id, type, position, state, content, created_at, updated_at)
        VALUES (
            '{_WORKSPACE_ROOT_ID}',
            NULL,
            NULL,
            'workspace',
            0.0,
            'active',
            '{{"title": "Workspace"}}',
            now(),
            now()
        );
    """)


def downgrade() -> None:
    op.execute(f"DELETE FROM blocks WHERE id = '{_WORKSPACE_ROOT_ID}'")

    for table in _TRIGGER_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")

    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")

    op.drop_index("ix_property_values_page_id", table_name="property_values")
    op.drop_table("property_values")

    op.drop_index(
        "ix_property_schemas_database_id", table_name="property_schemas"
    )
    op.drop_table("property_schemas")

    op.drop_index("ix_blocks_type", table_name="blocks")
    op.drop_index("ix_blocks_state", table_name="blocks")
    op.drop_index("ix_blocks_parent_id", table_name="blocks")
    op.drop_table("blocks")
