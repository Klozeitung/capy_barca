"""create wopi_tokens registry table

Revision ID: w3z5y4u60023
Revises: v2y4x3t50022
Create Date: 2026-10-19

Records every WOPI access token the server issues, so the file endpoints can
refuse a token the server did not issue or has withdrawn.

Schema changes
--------------
wopi_tokens
    jti         UUID PRIMARY KEY
        The token's own id, carried in its ``jti`` claim.
    user_id     UUID NOT NULL, FK users.id ON DELETE CASCADE, indexed
        The account the token was issued to. Indexed for the revocation on a
        password change.
    block_id    UUID NOT NULL, FK blocks.id ON DELETE CASCADE
        The Drive block holding the file.
    file_uuid   UUID NOT NULL
    session_key VARCHAR(64) NULL, FK sessions.token ON DELETE CASCADE, indexed
        The session the token was issued in, so logging out ends the editor
        sessions opened in it and no others.
    issued_at   TIMESTAMPTZ NOT NULL
    expires_at  TIMESTAMPTZ NOT NULL, indexed
        Indexed for the removal of expired rows.

Backfill
--------
None. Tokens issued before this revision carry no ``jti`` and are refused from
now on; Collabora asks for a new one when the document is opened again.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w3z5y4u60023"
down_revision: Union[str, None] = "v2y4x3t50022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wopi_tokens",
        sa.Column("jti", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("block_id", sa.Uuid(), nullable=False),
        sa.Column("file_uuid", sa.Uuid(), nullable=False),
        sa.Column("session_key", sa.String(64), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("jti"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["block_id"], ["blocks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_key"], ["sessions.token"], ondelete="CASCADE"),
    )
    op.create_index("ix_wopi_tokens_user_id", "wopi_tokens", ["user_id"])
    op.create_index("ix_wopi_tokens_session_key", "wopi_tokens", ["session_key"])
    op.create_index("ix_wopi_tokens_expires_at", "wopi_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_wopi_tokens_expires_at", table_name="wopi_tokens")
    op.drop_index("ix_wopi_tokens_session_key", table_name="wopi_tokens")
    op.drop_index("ix_wopi_tokens_user_id", table_name="wopi_tokens")
    op.drop_table("wopi_tokens")
