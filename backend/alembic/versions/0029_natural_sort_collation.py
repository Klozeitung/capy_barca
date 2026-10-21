"""natural sort order for text

Revision ID: c9f1e0a20029
Revises: b8e0d9z10028
Create Date: 2026-10-21

Text was sorted by the database's default collation, byte by byte: "1, 10,
19, 2, 20, 3", and every upper-case letter before every lower-case one. This
revision creates the collation ``natural_sort``, an ICU collation that
compares digit runs as numbers and orders letters as a dictionary does:
"1, 2, 3, 10, 19, 20" and "a, A, b, B". Text sorts of the entry queries use
it.

PostgreSQL has to be built with ICU support. The official ``postgres`` image
used by ``docker-compose.yml`` is; on an installation without ICU this
revision stops with an error that says so, instead of sorting differently
without notice.

Downgrade
---------
Drops the collation; the previous code does not use it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9f1e0a20029"
down_revision: Union[str, None] = "b8e0d9z10028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLLATION = "natural_sort"

# The ICU root locale with numeric ordering of digit runs (``kn``).
ICU_LOCALE = "und-u-kn"

MISSING_ICU = (
    "PostgreSQL was built without ICU support, which CapyBarca requires for "
    "sorting text. Use a PostgreSQL build with ICU, such as the official "
    "postgres image in docker-compose.yml."
)


def has_icu(connection: sa.engine.Connection) -> bool:
    """Whether the server offers ICU collations."""
    return bool(connection.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM pg_collation WHERE collprovider = 'i')"
    )))


def create(connection: sa.engine.Connection) -> None:
    """Create the collation; raise ``RuntimeError`` without ICU."""
    if not has_icu(connection):
        raise RuntimeError(MISSING_ICU)
    connection.execute(sa.text(
        f"CREATE COLLATION IF NOT EXISTS {COLLATION} "
        f"(provider = icu, locale = '{ICU_LOCALE}')"
    ))


def drop(connection: sa.engine.Connection) -> None:
    connection.execute(sa.text(f"DROP COLLATION IF EXISTS {COLLATION}"))


def upgrade() -> None:
    create(op.get_bind())


def downgrade() -> None:
    drop(op.get_bind())
