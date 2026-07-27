"""create media_files mapping table

Revision ID: q7t9s8o00017
Revises: p6s8r7n90016
Create Date: 2026-07-26

Records which block each uploaded file belongs to, for the storage categories
whose path does not say.

Schema changes
--------------
media_files
    file_uuid   UUID PRIMARY KEY
        The identifier the upload endpoint generated and handed back.
    block_id    UUID NOT NULL, indexed
        The block the file was uploaded against. Deliberately without a foreign
        key to blocks: the row records where a file came from rather than
        holding a live reference, and a constraint would turn an upload naming
        a block that does not exist into an integrity error deep in the
        request. Block ids are never reused, so a row outliving its block can
        only ever match the request it was written for.
    category    TEXT NOT NULL
    stored_name TEXT NOT NULL
        The name on disk, extension included.
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()

Backfill
--------
None, and none is possible. Files already on disk in the flat categories carry
no record of their owning block — that missing information is the defect this
table exists to prevent from recurring. The media router treats a file with no
row as admin-only rather than inventing an owner for it.

Drive files are not recorded here at all. They live under
drives/{block_id}/, so the directory already is the mapping.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q7t9s8o00017"
down_revision: Union[str, None] = "p6s8r7n90016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_files",
        sa.Column("file_uuid", sa.Uuid(), nullable=False),
        sa.Column("block_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("stored_name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("file_uuid"),
    )
    op.create_index("ix_media_files_block_id", "media_files", ["block_id"])


def downgrade() -> None:
    op.drop_index("ix_media_files_block_id", table_name="media_files")
    op.drop_table("media_files")
