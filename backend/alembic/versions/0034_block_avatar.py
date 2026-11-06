"""block avatar

Revision ID: b4e6d5f70034
Revises: a3d5c4e60033
Create Date: 2026-11-06

Adds ``blocks.avatar``, the static URL of a page's profile picture. The file
is a square image the client cropped before uploading and lives under
``static/uploads/avatars/`` as ``<block id><ext>``. Every page starts without
one, existing pages included.

Downgrade
---------
Drops the column. The image files stay on disk and are no longer referenced.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4e6d5f70034"
down_revision: Union[str, None] = "a3d5c4e60033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AVATAR_COLUMN = "avatar"


def upgrade() -> None:
    op.add_column("blocks", sa.Column(AVATAR_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("blocks", AVATAR_COLUMN)
