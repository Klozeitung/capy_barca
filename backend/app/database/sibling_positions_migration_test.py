"""
Tests for revision 0022, which renumbers siblings that share a position.

Each test seeds blocks inside a transaction, applies the revision, and rolls
everything back at the end, so the shared test database is left as the
session fixture built it.
"""
import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0022_sibling_positions.py"
_spec = importlib.util.spec_from_file_location("migration_0022", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

_START = datetime(2000, 7, 28, tzinfo=timezone.utc)


@pytest.fixture
def connection(test_engine):
    """A connection inside a transaction that is always rolled back."""
    with test_engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


def _run(connection, step) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        step()


def _block(
    connection,
    *,
    type_: str,
    position: float,
    parent_id: uuid.UUID | None = None,
    minutes: int = 0,
    state: str = "active",
    block_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert one block created *minutes* after a fixed start time."""
    block_id = block_id or uuid.uuid4()
    stamp = _START + timedelta(minutes=minutes)
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position, parent_id, state, created_at, updated_at) "
        "VALUES (:id, :type, :position, :parent, :state, :stamp, :stamp)"
    ), {
        "id": block_id, "type": type_, "position": position, "parent": parent_id,
        "state": state, "stamp": stamp,
    })
    return block_id


def _positions(connection, *ids: uuid.UUID) -> list[float]:
    rows = dict(connection.execute(
        sa.text("SELECT id, position FROM blocks WHERE id = ANY(:ids)"), {"ids": list(ids)},
    ).all())
    return [rows[i] for i in ids]


def test_revision_follows_the_permission_levels_revision():
    assert migration.revision == "v2y4x3t50022"
    assert migration.down_revision == "u1x3w2s40021"


def test_columns_at_one_position_are_numbered_in_creation_order(connection):
    page = _block(connection, type_="page", position=1.0)
    layout = _block(connection, type_="layout", position=1.0, parent_id=page)
    second = _block(connection, type_="column", position=1.0, parent_id=layout, minutes=2)
    first = _block(connection, type_="column", position=1.0, parent_id=layout, minutes=1)
    third = _block(connection, type_="column", position=1.0, parent_id=layout, minutes=3)

    _run(connection, migration.upgrade)

    assert _positions(connection, first, second, third) == [1.0, 2.0, 3.0]


def test_all_active_children_of_an_affected_parent_are_renumbered(connection):
    """Untied siblings keep their place relative to the tied ones."""
    page = _block(connection, type_="page", position=1.0)
    before = _block(connection, type_="paragraph", position=0.5, parent_id=page, minutes=5)
    tied_a = _block(connection, type_="paragraph", position=2.0, parent_id=page, minutes=1)
    tied_b = _block(connection, type_="paragraph", position=2.0, parent_id=page, minutes=2)
    after = _block(connection, type_="paragraph", position=7.0, parent_id=page, minutes=0)

    _run(connection, migration.upgrade)

    assert _positions(connection, before, tied_a, tied_b, after) == [1.0, 2.0, 3.0, 4.0]


def test_equal_creation_times_are_ordered_by_id(connection):
    """Blocks copied in one transaction share their creation time."""
    page = _block(connection, type_="page", position=1.0)
    low = uuid.UUID("00000000-0000-0000-0000-000000000001")
    high = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    _block(connection, type_="column", position=1.0, parent_id=page, block_id=high)
    _block(connection, type_="column", position=1.0, parent_id=page, block_id=low)

    _run(connection, migration.upgrade)

    assert _positions(connection, low, high) == [1.0, 2.0]


def test_parents_without_equal_positions_and_trashed_children_are_untouched(connection):
    page = _block(connection, type_="page", position=1.0)
    clean = [
        _block(connection, type_="paragraph", position=pos, parent_id=page, minutes=i)
        for i, pos in enumerate((0.25, 3.0, 9.5))
    ]
    layout = _block(connection, type_="layout", position=10.0, parent_id=page)
    active = _block(connection, type_="column", position=1.0, parent_id=layout, minutes=1)
    trashed = _block(
        connection, type_="column", position=1.0, parent_id=layout, minutes=2, state="trash",
    )

    _run(connection, migration.upgrade)

    assert _positions(connection, *clean) == [0.25, 3.0, 9.5]
    # A single active child at the shared position is no tie.
    assert _positions(connection, active, trashed) == [1.0, 1.0]


def test_downgrade_changes_nothing(connection):
    page = _block(connection, type_="page", position=1.0)
    column = _block(connection, type_="column", position=4.0, parent_id=page)

    _run(connection, migration.downgrade)

    assert _positions(connection, column) == [4.0]
