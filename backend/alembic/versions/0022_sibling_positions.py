"""renumber siblings that share a position

Revision ID: v2y4x3t50022
Revises: u1x3w2s40021
Create Date: 2026-10-19

Siblings under one parent are ordered by ``blocks.position``. Until now
several paths could give two active siblings the same position, most of all
the columns of a layout, which the client created with positions computed
from a list it had cached. PostgreSQL returns rows with equal sort keys in no
fixed order, so such siblings changed places between reads, and every move
into their parent rebalanced them in whatever order the read happened to
return.

For every parent with at least two active children at the same position,
this revision renumbers all of its active children to 1.0, 2.0, … in the
order position, created_at, id. That is the order the application now reads
siblings in (``repository.SIBLING_ORDER``), so the order shown after the
upgrade is the order that stays. Parents without equal positions and
trashed children are left untouched. The ``updated_at`` trigger stamps the
renumbered rows as it does for every rebalance.

Downgrade
---------
Nothing to undo: the previous positions held no order that could be
restored, and the renumbered ones are valid for the previous revision.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v2y4x3t50022"
down_revision: Union[str, None] = "u1x3w2s40021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RENUMBER = sa.text("""
    UPDATE blocks AS b
    SET position = ranked.rank
    FROM (
        SELECT id,
               row_number() OVER (
                   PARTITION BY parent_id ORDER BY position, created_at, id
               ) AS rank
        FROM blocks
        WHERE state = 'active'
          AND parent_id IN (
              SELECT parent_id
              FROM blocks
              WHERE state = 'active' AND parent_id IS NOT NULL
              GROUP BY parent_id, position
              HAVING count(*) > 1
          )
    ) AS ranked
    WHERE b.id = ranked.id
      AND b.position <> ranked.rank
""")


def upgrade() -> None:
    op.execute(RENUMBER)


def downgrade() -> None:
    pass
