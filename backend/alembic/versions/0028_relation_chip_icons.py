"""add entry icons to stored relation chips of formulas and rollups

Revision ID: b8e0d9z10028
Revises: a7d9c8y00027
Create Date: 2026-10-20

A formula that passes a relation through, and a rollup that shows the linked
entries of a relation (``show_original``, ``first_value``, ``last_value``),
store their result as chip descriptors ``{"id", "title", "database_id"}``,
marked with ``"relation": true``. The descriptors now also carry the linked
entry's ``icon`` so the chips show it.

Stored results gain the icon only when they are next computed. This revision
adds it to every stored descriptor that lacks one, with the icon the entry has
now (null when it has none), so existing chips show it at once. Descriptors
are otherwise left as they are; an id that no longer names a block gets a null
icon.

The rewrite is copied here rather than imported, so the revision keeps
producing the same result however the application code changes later.

Downgrade
---------
Removes the ``icon`` key from stored descriptors again.
"""
import json
import logging
import uuid
from typing import Any, Callable, Mapping, Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8e0d9z10028"
down_revision: Union[str, None] = "a7d9c8y00027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")


# ─── Rewriting (pure, no database access) ─────────────────────────────────────


def _is_descriptor(item: Any) -> bool:
    return isinstance(item, dict) and "id" in item and "title" in item


def descriptor_ids(value: Any) -> list[str]:
    """The entry ids of the chip descriptors in a stored computed *value*."""
    if not isinstance(value, dict) or value.get("relation") is not True:
        return []
    result = value.get("result")
    items = result if isinstance(result, list) else [result]
    return [str(item["id"]) for item in items if _is_descriptor(item)]


def _map_descriptors(value: Any, change: Callable[[dict], dict]) -> Any:
    """*value* with *change* applied to each of its chip descriptors."""
    if not isinstance(value, dict) or value.get("relation") is not True:
        return value
    result = value.get("result")
    if isinstance(result, list):
        new_result: Any = [change(item) if _is_descriptor(item) else item for item in result]
    elif _is_descriptor(result):
        new_result = change(result)
    else:
        return value
    return {**value, "result": new_result}


def with_icons(value: Any, icons: Mapping[str, Optional[str]]) -> Any:
    """*value* with ``icon`` added to every descriptor lacking one, from *icons*."""
    return _map_descriptors(
        value,
        lambda item: item if "icon" in item else {**item, "icon": icons.get(str(item["id"]))},
    )


def without_icons(value: Any) -> Any:
    """*value* with ``icon`` removed from every descriptor."""
    return _map_descriptors(
        value, lambda item: {k: v for k, v in item.items() if k != "icon"},
    )


# ─── Database access ──────────────────────────────────────────────────────────


def _is_uuid(raw: str) -> bool:
    try:
        uuid.UUID(raw)
    except ValueError:
        return False
    return True


def _load(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _relation_results(connection: sa.engine.Connection) -> list[tuple[Any, Any]]:
    rows = connection.execute(sa.text(
        "SELECT pv.id, pv.value FROM property_values pv "
        "JOIN property_schemas ps ON ps.id = pv.property_schema_id "
        "WHERE ps.type IN ('formula', 'rollup') AND pv.value IS NOT NULL"
    )).all()
    return [(row.id, _load(row.value)) for row in rows]


def _write(connection: sa.engine.Connection, rewritten: list[tuple[Any, Any]]) -> None:
    update = sa.text(
        "UPDATE property_values SET value = CAST(:value AS jsonb) WHERE id = :id"
    )
    for value_id, value in rewritten:
        connection.execute(update, {"id": value_id, "value": json.dumps(value)})


def migrate(connection: sa.engine.Connection) -> int:
    """Add icons to stored chip descriptors; returns how many values changed."""
    values = _relation_results(connection)
    wanted = {
        entry_id for _, value in values for entry_id in descriptor_ids(value)
        if _is_uuid(entry_id)
    }
    icons: dict[str, Optional[str]] = {}
    if wanted:
        rows = connection.execute(
            sa.text("SELECT id, icon FROM blocks WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": sorted(wanted)},
        )
        icons = {str(row.id): row.icon for row in rows}
    rewritten = []
    for value_id, value in values:
        after = with_icons(value, icons)
        if after != value:
            rewritten.append((value_id, after))
    _write(connection, rewritten)
    return len(rewritten)


def revert(connection: sa.engine.Connection) -> int:
    """Remove the icons again; returns how many values changed."""
    rewritten = []
    for value_id, value in _relation_results(connection):
        after = without_icons(value)
        if after != value:
            rewritten.append((value_id, after))
    _write(connection, rewritten)
    return len(rewritten)


def upgrade() -> None:
    changed = migrate(op.get_bind())
    if changed:
        log.info("relation chip icon migration updated %d value(s)", changed)


def downgrade() -> None:
    changed = revert(op.get_bind())
    if changed:
        log.info("relation chip icon migration reverted %d value(s)", changed)
