"""
Serialisation of concurrent Alembic runs.

The backend container migrates on every start, and setup, a manual call or a
restart loop can start another run at the same moment. Without a lock both
read the same old revision and apply the same migrations in parallel; the
second one fails on objects the first has just created.

``backend/alembic/env.py`` therefore takes a PostgreSQL advisory lock inside
the migration transaction before Alembic reads the current revision. A second
run waits there, reads the revision only after the first has committed, finds
the database at head and does nothing.
"""
from typing import Any

import sqlalchemy as sa

# Key of the advisory lock that serialises migration runs. Any fixed 64-bit
# value works as long as nothing else in the database uses it; this one is the
# ASCII text "CapyBarc".
MIGRATION_LOCK_KEY = 0x4361707942617263

_LOCK_STATEMENT = sa.text("SELECT pg_advisory_xact_lock(:key)")


def acquire_migration_lock(connection: Any) -> bool:
    """
    Wait for the migration lock on *connection* and return whether one was taken.

    The lock is transaction-scoped, so it is released on commit or rollback,
    also when the process dies. Only PostgreSQL has advisory locks; on any
    other database nothing is executed and False is returned.
    """
    if connection.dialect.name != "postgresql":
        return False
    connection.execute(_LOCK_STATEMENT, {"key": MIGRATION_LOCK_KEY})
    return True
