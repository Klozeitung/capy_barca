"""
Serialisation of relation writes.

A bilateral relation is stored twice: the value the user edits and its mirror
on every linked entry. Keeping the mirror right means reading it, changing it
and writing it back. Two transactions that do this for the same mirror at the
same time both read the old value, and the second commit silently discards the
first one's change. Two rows that link the same target in quick succession are
enough for that, as are two tabs or two users. A second symptom is a unique
violation when both try to create the same mirror value.

Every transaction that writes relation values therefore first takes a
transaction-scoped PostgreSQL advisory lock for each database whose relation
values it may change: the database it writes to and the target databases of
its bilateral relations. The locks are released on commit or rollback, also
when the process dies.

Rules for callers
-----------------
* Lock before reading anything the write depends on, in particular the old
  value a diff is computed against. A read made before the lock may be stale.
* Lock once per transaction, with every database the transaction needs. The
  keys are taken in ascending order, so two transactions can never wait for
  each other in a cycle. A second call later in the same transaction could
  take a lower key after a higher one and break that guarantee.

Only PostgreSQL has advisory locks. On any other database nothing is executed.
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.blocks import repository as repo

# Namespace of the two-key advisory locks taken here. The two-key form lives in
# a lock space of its own, apart from the single-key locks of migrations and
# registration. Any fixed 32-bit value works as long as nothing else uses it in
# that space; this one is the ASCII text "CapR".
RELATION_LOCK_NAMESPACE = 0x43617052

_LOCK_STATEMENT = sa.text("SELECT pg_advisory_xact_lock(:namespace, :key)")

# Property types whose values link entries and may rewrite a mirror.
RELATION_FAMILY_TYPES = frozenset({"relation", "parent_item", "sub_item"})

_BILATERAL_DIRECTIONS = frozenset({"bilateral", "bilateral_self"})


def lock_key(database_id: uuid.UUID) -> int:
    """
    Return the 32-bit key for *database_id*: the first four bytes of the UUID
    as a signed integer. Two databases sharing a key only wait for each other
    without need; correctness does not depend on keys being unique.
    """
    return int.from_bytes(database_id.bytes[:4], "big", signed=True)


def _target_database_id(config: Optional[dict]) -> Optional[uuid.UUID]:
    raw = (config or {}).get("target_database_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def databases_for_schema(database_id: uuid.UUID, schema: Any) -> set[uuid.UUID]:
    """
    Return the databases a write to *schema* in *database_id* may change:
    the database itself and, for a bilateral relation, its target database.
    """
    databases = {database_id}
    config = schema.config or {}
    if schema.type == "relation" and config.get("direction") in _BILATERAL_DIRECTIONS:
        target = _target_database_id(config)
        if target is not None:
            databases.add(target)
    return databases


def databases_for_database(db: Session, database_id: uuid.UUID) -> set[uuid.UUID]:
    """
    Return the databases that writing any relation value of an entry of
    *database_id* may change: the database itself and the targets of all its
    bilateral relations. Used when a whole entry is written or cleaned up.
    """
    databases = {database_id}
    for schema in repo.list_schemas(db, database_id):
        databases |= databases_for_schema(database_id, schema)
    return databases


def lock_relation_databases(db: Session, database_ids: Iterable[uuid.UUID]) -> list[int]:
    """
    Wait for the relation locks of *database_ids* in this transaction and
    return the keys taken, in the order they were taken.

    Returns an empty list without touching the database when there is nothing
    to lock or the database is not PostgreSQL.
    """
    keys = sorted({lock_key(database_id) for database_id in database_ids})
    if not keys or db.get_bind().dialect.name != "postgresql":
        return []
    for key in keys:
        db.execute(_LOCK_STATEMENT, {"namespace": RELATION_LOCK_NAMESPACE, "key": key})
    return keys
