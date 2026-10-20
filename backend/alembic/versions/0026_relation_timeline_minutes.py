"""recompute relation timelines at minute resolution

Revision ID: z6c8b7x90026
Revises: y5b7a6w80025
Create Date: 2026-10-20

A timelined relation stores what the user entered as ``relationPool`` (and
``nuancePool``) and the slots derived from it as ``_timeline``. The derivation
treated a range's end as an instant and placed the next change-point one
second after it, while the editor enters boundaries in whole minutes. A range
ending at 14:14 followed by one starting at 14:15 therefore produced a
59-second slot from 14:14:01 to 14:14:59 that held only the links neither
ending nor starting in that minute, and the slot after a final end started at
hh:mm:01.

The derivation now works in minutes: an end covers its minute, and slot ends
are the last minute a slot covers. This revision recomputes ``_timeline`` for
every stored relation value that carries a ``relationPool`` (entries and
templates, both sides of bilateral relations), so the extra slots disappear
without each value having to be saved again. ``relationPool`` and
``nuancePool`` are left as they are.

The derivation is copied here rather than imported, so the revision keeps
producing the same result however the application code changes later.

Downgrade
---------
Nothing to undo: the recomputed slots are valid timelines for the previous
code as well, which rederives them on the next write anyway.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "z6c8b7x90026"
down_revision: Union[str, None] = "y5b7a6w80025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

_MINUTE = timedelta(minutes=1)


# ─── Derivation (pure, no database access) ────────────────────────────────────


def _parse_range(range_str: str) -> tuple[Optional[str], Optional[str]]:
    """``"start→end"`` into its two sides; None for an open side."""
    if "→" in range_str:
        start, end = range_str.split("→", 1)
        return start.strip() or None, end.strip() or None
    return range_str.strip() or None, None


def _parse_ts(ts: str) -> Optional[datetime]:
    """A boundary as a naive datetime truncated to the minute, or None."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    return dt.replace(tzinfo=None, second=0, microsecond=0)


def _fmt_ts(dt: datetime) -> str:
    """Canonical boundary with a zero-padded four-digit year."""
    return (
        f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
        f"T{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
    )


def pool_to_timeline(pool: Any, nuance_pool: Any = None) -> dict:
    """
    The ``_timeline`` slots of a relation pool, at minute resolution.

    Always-valid links (range ``""``) appear in every slot; when there are no
    bounded ranges they form the single ``""`` slot. An end covers its whole
    minute. Unparseable boundaries and ranges ending before they start are
    skipped. A slot's ``nuances`` maps its links to the label stored for the
    range that makes them active, the always-valid label taking precedence.
    """
    if not isinstance(pool, dict) or not pool:
        return {}
    labels: dict = nuance_pool if isinstance(nuance_pool, dict) else {}

    def label_for(uid: str, range_str: str) -> Optional[str]:
        ranges = labels.get(uid)
        if not isinstance(ranges, dict):
            return None
        return ranges.get(range_str) or None

    always: list[str] = []
    always_labels: dict[str, str] = {}
    parsed: list[tuple[str, Optional[datetime], Optional[datetime], str]] = []

    for uid, ranges in pool.items():
        for r in ranges if isinstance(ranges, list) else []:
            if not isinstance(r, str):
                continue
            if r == "":
                if uid not in always:
                    always.append(uid)
                lbl = label_for(uid, "")
                if lbl and uid not in always_labels:
                    always_labels[uid] = lbl
                continue
            s_raw, e_raw = _parse_range(r)
            start = _parse_ts(s_raw) if s_raw is not None else None
            if s_raw is not None and start is None:
                continue
            end_exclusive: Optional[datetime] = None
            if e_raw is not None:
                end = _parse_ts(e_raw)
                if end is None:
                    continue
                try:
                    end_exclusive = end + _MINUTE
                except OverflowError:
                    end_exclusive = None
            if start is not None and end_exclusive is not None and end_exclusive <= start:
                continue
            parsed.append((uid, start, end_exclusive, r))

    def build_slot(pairs: list[tuple[str, str]]) -> dict:
        bounded: list[str] = []
        for uid, _r in pairs:
            if uid not in bounded:
                bounded.append(uid)
        related = always + [u for u in bounded if u not in always]
        nuances = dict(always_labels)
        for uid, r in pairs:
            if uid in nuances:
                continue
            lbl = label_for(uid, r)
            if lbl:
                nuances[uid] = lbl
        slot: dict = {"related_ids": related}
        if nuances:
            slot["nuances"] = nuances
        return slot

    def always_only() -> dict:
        if not always:
            return {}
        slot: dict = {"related_ids": list(always)}
        if always_labels:
            slot["nuances"] = dict(always_labels)
        return {"": slot}

    if not parsed:
        return always_only()

    points: set[datetime] = set()
    for _uid, s, e, _r in parsed:
        if s is not None:
            points.add(s)
        if e is not None:
            points.add(e)
    if not points:
        return always_only()
    ordered = sorted(points)

    timeline: dict = {}

    if always or any(s is None for _uid, s, _e, _r in parsed):
        first = ordered[0]
        try:
            before_end: Optional[datetime] = first - _MINUTE
        except OverflowError:
            before_end = None
        if before_end is not None:
            slot = build_slot([
                (uid, r) for uid, s, e, r in parsed
                if s is None and (e is None or e >= first)
            ])
            if slot["related_ids"]:
                timeline[f"→{_fmt_ts(before_end)}"] = slot

    for i, point in enumerate(ordered):
        pairs = [
            (uid, r) for uid, s, e, r in parsed
            if (s is None or s <= point) and (e is None or point < e)
        ]
        slot = build_slot(pairs)
        if not slot["related_ids"]:
            continue
        if i < len(ordered) - 1:
            key = f"{_fmt_ts(point)}→{_fmt_ts(ordered[i + 1] - _MINUTE)}"
        else:
            active = {u for u, _r in pairs}
            still_open = bool(always) or any(
                e is None for uid, s, e, _r in parsed
                if uid in active and (s is None or s <= point)
            )
            key = f"{_fmt_ts(point)}→" if still_open else f"{_fmt_ts(point)}→{_fmt_ts(point)}"
        timeline[key] = slot

    return timeline


def recompute_value(value: Any) -> Any:
    """*value* with its ``_timeline`` rederived from its pool; else unchanged."""
    if not isinstance(value, dict) or not isinstance(value.get("relationPool"), dict):
        return value
    return {
        **value,
        "_timeline": pool_to_timeline(value["relationPool"], value.get("nuancePool")),
    }


# ─── Database access ──────────────────────────────────────────────────────────


def _load(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def migrate(connection: sa.engine.Connection) -> int:
    """Rederive every stored relation timeline; returns how many changed."""
    rows = connection.execute(sa.text(
        "SELECT pv.id, pv.value FROM property_values pv "
        "JOIN property_schemas ps ON ps.id = pv.property_schema_id "
        "WHERE ps.type = 'relation' AND pv.value IS NOT NULL"
    )).all()
    update = sa.text(
        "UPDATE property_values SET value = CAST(:value AS jsonb) WHERE id = :id"
    )
    changed = 0
    for row in rows:
        before = _load(row.value)
        after = recompute_value(before)
        if after != before:
            connection.execute(update, {"id": row.id, "value": json.dumps(after)})
            changed += 1
    return changed


def upgrade() -> None:
    changed = migrate(op.get_bind())
    if changed:
        log.info("relation timeline migration recomputed %d value(s)", changed)


def downgrade() -> None:
    pass
