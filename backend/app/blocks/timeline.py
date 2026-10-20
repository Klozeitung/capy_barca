"""
Timeline values of database properties.

A property with ``config.hasTimeline`` stores its value per period instead of
as one value: ``{"_timeline": {period_key: slot}}``. Period keys are
``""`` (always valid, only as the sole key), ``"→end"``, ``"start→"`` and
``"start→end"``.

Relation properties do not store their slots directly. The client writes a
``relationPool`` (linked entry to its ranges) and optionally a ``nuancePool``
(linked entry and range to a nuance label); :func:`pool_to_timeline` derives
the ``_timeline`` slots from them.

Contents
--------
parse_pool_range                    – split a range or period key into its sides
pool_to_timeline                    – derive the slots of a relation pool
sanitize_nuance_pool                – keep only nuance labels of existing ranges
sanitize_flat_nuances               – the same for a relation without timeline
validate_timeline_value             – structure check of a scalar timeline
extract_related_ids_now             – current links of a relation value
migrate_relation_values_to_timeline – convert relation values when a timeline
                                      is enabled
migrate_scalar_values_to_timeline   – the same for every other property type
"""
import re
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.blocks import repository as repo


def parse_pool_range(range_str: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parse a pool range string into ``(start, end)`` ISO timestamp strings.

    ``None`` on either side means open (−∞ or +∞).

    Examples::

        "2024-01-01T00:00:00→2024-12-31T23:59:59"  →  ("2024-01-01T00:00:00", "2024-12-31T23:59:59")
        "2025-01-01T00:00:00→"                      →  ("2025-01-01T00:00:00", None)
        "→2023-12-31T23:59:59"                      →  (None, "2023-12-31T23:59:59")
        ""                                           →  (None, None)
    """
    if "→" in range_str:
        parts = range_str.split("→", 1)
        start = parts[0].strip() or None
        end = parts[1].strip() or None
    else:
        # No arrow: treat the whole string as a start (open-ended "since").
        start = range_str.strip() or None
        end = None
    return start, end


def pool_to_timeline(pool: dict, nuance_pool: Optional[dict] = None) -> dict:
    """
    Compute the ``_timeline`` dict from a ``relationPool``.

    Handles three range formats in the pool:

    * ``""`` (always-valid) – the UUID is linked for all time.
    * ``"start→"`` / ``"start→end"`` / ``"→end"`` – bounded ranges.

    Resolution
    ----------
    Timeline boundaries have a resolution of one minute, the precision the
    timeline editor enters them in.  A start covers its minute, and an end
    covers its minute as well: a range ending at 14:14 is linked through
    14:14:59, so a following range starting at 14:15 joins it without a gap.
    Seconds and fractions in stored boundaries are ignored, as is a UTC offset
    (boundaries are wall-clock times).  A date without a time is midnight.

    Every boundary is parsed before it is compared, so the same instant written
    in two forms (``2000-01-01`` and ``2000-01-01T00:00:00``) is one
    change-point.  Derived slot keys always use the canonical
    ``YYYY-MM-DDTHH:MM:00`` form with a zero-padded year, and a slot's end is the
    last minute it covers.

    Rules
    -----
    * When **only** always-valid ranges exist the result is a single ``""``
      slot (the only sentinel allowed as a sole entry in ``_timeline``).
    * When bounded ranges exist, always-valid UIDs are merged into every
      computed slot, including a leading ``"→t"`` slot that covers the time
      before the earliest change-point and a trailing open slot after the
      last bounded end.  The ``""`` sentinel is never emitted alongside
      bounded slots (the spec prohibits mixing).
    * Invalid or unparseable timestamps are skipped silently, as are ranges
      that end before they start (they cover no time).

    Nuance
    ------
    ``nuance_pool`` is an optional ``{ uid: { range_str: label } }`` map that
    runs parallel to *pool*.  When supplied, each computed slot gains a
    ``nuances`` sub-dict mapping the UIDs active in that slot to their nuance
    label for the matching range.  A UID active via its always-valid (``""``)
    range carries the label registered under ``""``; if the same UID is also
    bounded with a different label, the always-valid label wins (mirroring the
    related_ids dedup, which lists the always-valid UID first).  UIDs with no
    label are omitted, and a slot with no nuanced UID carries no ``nuances``
    key — so the output is byte-identical to the nuance-free form whenever
    *nuance_pool* is empty.
    """
    from datetime import datetime as _DT, timedelta as _TD

    if not pool:
        return {}

    minute = _TD(minutes=1)
    np_map: dict = nuance_pool or {}

    def _nuance_for(uid: str, range_str: str) -> Optional[str]:
        ranges = np_map.get(uid)
        if not isinstance(ranges, dict):
            return None
        label = ranges.get(range_str)
        return label or None

    def _parse_ts(ts: str) -> Optional[_DT]:
        """A boundary as a naive datetime truncated to the minute, or None."""
        try:
            dt = _DT.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            return None
        return dt.replace(tzinfo=None, second=0, microsecond=0)

    def _fmt_ts(dt: _DT) -> str:
        # strftime("%Y") does not zero-pad years < 1000 on glibc, so a date
        # like year 44 would render as "44-..." instead of "0044-...".  Slot
        # keys are ordered by plain string comparison elsewhere (the frontend
        # and _last_timeline_slot), and an unpadded year breaks that ordering.
        # Format the components explicitly to guarantee a fixed-width year.
        return (
            f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
            f"T{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
        )

    # ── Separate always-valid from time-bounded ranges ────────────────────────
    always_uids: list[str] = []
    always_nuance: dict[str, str] = {}
    # (uid, start, end_exclusive, range_str); None means open on that side.
    parsed: list[tuple[str, Optional[_DT], Optional[_DT], str]] = []

    for uid, uid_ranges in pool.items():
        for r in (uid_ranges or []):
            if r == "":
                if uid not in always_uids:
                    always_uids.append(uid)
                lbl = _nuance_for(uid, "")
                if lbl and uid not in always_nuance:
                    always_nuance[uid] = lbl
                continue
            s_raw, e_raw = parse_pool_range(r)
            start = _parse_ts(s_raw) if s_raw is not None else None
            if s_raw is not None and start is None:
                continue
            end_exclusive: Optional[_DT] = None
            if e_raw is not None:
                end = _parse_ts(e_raw)
                if end is None:
                    continue
                try:
                    end_exclusive = end + minute
                except OverflowError:
                    # The end is the last representable minute: the range
                    # never ends.
                    end_exclusive = None
            if start is not None and end_exclusive is not None and end_exclusive <= start:
                continue
            parsed.append((uid, start, end_exclusive, r))

    def _build_slot(bounded_pairs: list[tuple[str, str]]) -> dict:
        """Assemble a slot dict (related_ids + optional nuances) for a set of
        (uid, range_str) pairs, with always-valid UIDs merged in front."""
        bounded_uids: list[str] = []
        for uid, _r in bounded_pairs:
            if uid not in bounded_uids:
                bounded_uids.append(uid)
        related = always_uids + [u for u in bounded_uids if u not in always_uids]
        nuances: dict[str, str] = dict(always_nuance)
        for uid, r in bounded_pairs:
            if uid in nuances:
                continue
            lbl = _nuance_for(uid, r)
            if lbl:
                nuances[uid] = lbl
        slot: dict = {"related_ids": related}
        if nuances:
            slot["nuances"] = nuances
        return slot

    def _always_only() -> dict:
        if always_uids:
            slot: dict = {"related_ids": list(always_uids)}
            if always_nuance:
                slot["nuances"] = dict(always_nuance)
            return {"": slot}
        return {}

    # ── No bounded ranges → single always-valid slot (or empty) ──────────────
    if not parsed:
        return _always_only()

    # ── Sweepline over bounded ranges ─────────────────────────────────────────
    change_points: set[_DT] = set()
    for _uid, s, e, _r in parsed:
        if s is not None:
            change_points.add(s)
        if e is not None:
            change_points.add(e)

    if not change_points:
        # Only ranges open on both sides, which cannot be written ("" is the
        # always-valid form), but would cover all time.
        return _always_only()

    sorted_cps = sorted(change_points)

    def _bounded_active_at(ts: _DT) -> list[tuple[str, str]]:
        return [
            (uid, r) for uid, s, e, r in parsed
            if (s is None or s <= ts) and (e is None or ts < e)
        ]

    timeline: dict = {}

    # Leading slot before the first change-point. It is populated by "until"
    # ranges (start=None) and by always-valid UIDs, which are active at every
    # point in time and therefore also before the earliest bounded start.
    # Without the always-valid case a pool such as {A: [""], B: ["t→"]} would
    # collapse into a single "t→" slot and lose the period in which only A
    # is linked.
    has_until = any(s is None for _uid, s, e, _r in parsed)
    if has_until or always_uids:
        first_cp = sorted_cps[0]
        try:
            before_end: Optional[_DT] = first_cp - minute
        except OverflowError:
            # The first change-point is the earliest representable minute:
            # no time lies before it.
            before_end = None
        if before_end is not None:
            before_pairs = [
                (uid, r) for uid, s, e, r in parsed
                if s is None and (e is None or e >= first_cp)
            ]
            slot = _build_slot(before_pairs)
            if slot["related_ids"]:
                timeline[f"→{_fmt_ts(before_end)}"] = slot

    n = len(sorted_cps)
    for i, cp in enumerate(sorted_cps):
        bounded_pairs = _bounded_active_at(cp)
        slot = _build_slot(bounded_pairs)
        if not slot["related_ids"]:
            continue

        if i < n - 1:
            slot_end = sorted_cps[i + 1] - minute
            slot_key = f"{_fmt_ts(cp)}→{_fmt_ts(slot_end)}"
        else:
            # Last change-point: open if any bounded-active uid has no end,
            # or if there are always-valid uids (they never end).
            bounded_uids_now = {u for u, _ in bounded_pairs}
            still_open = bool(always_uids) or any(
                e is None
                for uid, s, e, _r in parsed
                if uid in bounded_uids_now and (s is None or s <= cp)
            )
            slot_key = f"{_fmt_ts(cp)}→" if still_open else f"{_fmt_ts(cp)}→{_fmt_ts(cp)}"

        timeline[slot_key] = slot

    return timeline


def sanitize_nuance_pool(raw: object, pool: dict) -> dict:
    """
    Coerce a client-supplied ``nuancePool`` into ``{ uid: { range_str: label } }``.

    Only (uid, range) pairs that actually exist in *pool* and carry a non-empty
    string label are kept.  Returns ``{}`` when nothing valid remains, so the
    caller can omit the key entirely.
    """
    if not isinstance(raw, dict):
        return {}
    valid_pairs = {
        (uid, r) for uid, ranges in pool.items() for r in (ranges or [])
    }
    out: dict = {}
    for uid, ranges in raw.items():
        if not isinstance(ranges, dict):
            continue
        for r, label in ranges.items():
            if (uid, r) not in valid_pairs:
                continue
            if not isinstance(label, str) or not label.strip():
                continue
            out.setdefault(uid, {})[r] = label
    return out


def sanitize_flat_nuances(raw: object, related_ids: list[str]) -> dict:
    """
    Coerce a client-supplied flat ``nuances`` map into ``{ uid: label }``.

    Only UIDs present in *related_ids* with a non-empty string label are kept.
    """
    if not isinstance(raw, dict):
        return {}
    allowed = set(related_ids)
    out: dict = {}
    for uid, label in raw.items():
        if uid not in allowed:
            continue
        if not isinstance(label, str) or not label.strip():
            continue
        out[uid] = label
    return out


_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?")


def _is_valid_ts(ts: str) -> bool:
    """Return True if *ts* looks like a valid ISO 8601 datetime string."""
    return bool(_TS_RE.match(ts))


def validate_timeline_value(value: dict) -> Optional[str]:
    """
    Validate the structure of a ``_timeline``-wrapped property value.

    Returns an error message string on failure, or ``None`` on success.

    Rules enforced
    --------------
    * ``_timeline`` must be a dict.
    * The empty-string key ``""`` is only permitted as the sole entry.
    * At most one ``"→date"`` (until) key, only as the first entry.
    * At most one ``"date→"`` (since) key, only as the last entry.
    * All other keys must be ``"date→date"`` with valid ISO timestamps.
    * No overlapping ranges (end[i] >= start[i+1] is a violation).
    """
    timeline = value.get("_timeline")
    if not isinstance(timeline, dict):
        return "_timeline must be a JSON object"

    keys = list(timeline.keys())
    if not keys:
        return None  # empty timeline is valid

    # Special case: single "" entry
    if "" in keys:
        if len(keys) != 1:
            return "The '' (always-valid) key is only permitted as the sole timeline entry"
        return None

    # Parse all keys
    parsed: list[tuple[str, Optional[str], Optional[str]]] = []  # (key, start, end)
    for k in keys:
        s, e = parse_pool_range(k)
        # Validate individual timestamps
        for ts in (s, e):
            if ts is not None and not _is_valid_ts(ts):
                return f"Invalid timestamp in timeline key '{k}': '{ts}'"
        parsed.append((k, s, e))

    # Sort by start (None = −∞ sorts first via empty string proxy)
    def _sort_key(item: tuple) -> str:
        s = item[1]
        return s if s is not None else ""

    parsed.sort(key=_sort_key)

    n = len(parsed)
    for i, (k, s, e) in enumerate(parsed):
        is_first = i == 0
        is_last = i == n - 1

        # "→date" (until): only allowed as first entry
        if s is None and e is not None and not is_first:
            return f"'→{e}' (until-range) is only allowed as the oldest (first) entry"

        # "date→" (since): only allowed as last entry
        if s is not None and e is None and not is_last:
            return f"'{s}→' (since-range) is only allowed as the youngest (last) entry"

        # Overlap check against the next entry
        if not is_last:
            _k2, s2, _e2 = parsed[i + 1]
            e_cmp = e if e is not None else "\xff" * 30  # +∞ sentinel
            s2_cmp = s2 if s2 is not None else ""
            if e_cmp >= s2_cmp:
                return f"Timeline ranges overlap: '{k}' and '{_k2}'"

    return None


def extract_related_ids_now(value: Optional[dict]) -> list[str]:
    """
    Extract the current flat ``related_ids`` list from a relation value.

    For timeline relations (``_timeline`` present): returns the list from the
    chronologically last slot.  For plain relations: returns ``related_ids``
    from the root dict.  Always returns a plain Python list of strings.
    """
    if not value:
        return []
    if "_timeline" in value:
        # Import here to avoid circular dependency risk
        from app.blocks.computed import _last_timeline_slot
        slot = _last_timeline_slot(value.get("_timeline") or {})
        if slot is None:
            return []
        return list(slot.get("related_ids", []))
    return list(value.get("related_ids", []))


def migrate_relation_values_to_timeline(
    db: Session,
    database_id: uuid.UUID,
    schema_id: uuid.UUID,
) -> None:
    """
    Migrate flat ``{ related_ids: [...] }`` relation values to the timeline
    pool format when ``hasTimeline`` is enabled on an existing relation schema.

    For each active entry in *database_id* that has a value for *schema_id*
    in the old flat format (``related_ids`` present, ``relationPool`` absent),
    the value is converted to::

        {
            "relationPool": { uid: [""] for uid in related_ids },
            "_timeline":    { "": { "related_ids": [...] } },
        }

    All previously linked entries become permanently "always valid" (``""``
    range), matching the ``migrateWarning`` shown in the UI before the user
    confirms the toggle.

    Values that are already in pool format, are ``None``, or have an empty
    ``related_ids`` list are left untouched.

    Parameters
    ----------
    db:
        Active database session (within the caller's transaction).
    database_id:
        UUID of the database whose entries should be scanned.
    schema_id:
        UUID of the relation schema that was just enabled for timeline.
    """
    entries = repo.list_children(
        db, database_id, state="active",
        exclude_types=frozenset({"entry_template"}),
    )
    for entry in entries:
        pv = repo.get_value(db, entry.id, schema_id)
        if pv is None or not pv.value:
            continue
        val = pv.value
        # Skip values already in pool/timeline format.
        if "relationPool" in val or "_timeline" in val:
            continue
        related_ids: list[str] = list(val.get("related_ids") or [])
        if not related_ids:
            continue
        flat_nuances = sanitize_flat_nuances(val.get("nuances"), related_ids)
        pool = {uid: [""] for uid in related_ids}
        nuance_pool = {
            uid: {"": flat_nuances[uid]}
            for uid in related_ids
            if flat_nuances.get(uid)
        }
        timeline = pool_to_timeline(pool, nuance_pool)
        value: dict = {"relationPool": pool}
        if nuance_pool:
            value["nuancePool"] = nuance_pool
        value["_timeline"] = timeline
        repo.upsert_value(
            db,
            page_id=entry.id,
            schema_id=schema_id,
            value=value,
        )


def migrate_scalar_values_to_timeline(
    db: Session,
    database_id: uuid.UUID,
    schema_id: uuid.UUID,
) -> None:
    """
    Migrate a flat scalar property value to the timeline format when
    ``hasTimeline`` is enabled on an existing non-relation schema.

    For each active entry in *database_id* that has a value for *schema_id*
    in the old flat format — any scalar shape such as ``{"text": ...}``,
    ``{"number": ...}``, ``{"option": ...}`` / ``{"options": [...]}``,
    ``{"start": ..., "end": ...}``, ``{"checked": ...}``, ``{"value": ...}``
    or ``{"files": [...]}`` without a ``_timeline`` key — the whole value is
    wrapped into a single always-valid (``""``) slot::

        { "_timeline": { "": <original flat value> } }

    The previous value becomes a permanent "always valid" (``""``) slot,
    matching the ``migrateWarning`` shown in the UI before the user confirms
    the toggle.  Without this migration, "all" display mode — which renders
    only ``_timeline`` slots — shows nothing for pre-existing entries, even
    though "last" mode still resolves the flat value.  The resolved "last"
    value is unchanged (a singleton ``""`` slot resolves back to the original
    value), so formula / rollup results are unaffected.

    Values that already contain a ``_timeline`` (or ``relationPool``) key,
    are ``None``, or are empty dicts are left untouched.

    Parameters
    ----------
    db:
        Active database session (within the caller's transaction).
    database_id:
        UUID of the database whose entries should be scanned.
    schema_id:
        UUID of the scalar schema that was just enabled for timeline.
    """
    entries = repo.list_children(
        db, database_id, state="active",
        exclude_types=frozenset({"entry_template"}),
    )
    for entry in entries:
        pv = repo.get_value(db, entry.id, schema_id)
        if pv is None or not pv.value:
            continue
        val = pv.value
        # Skip values already in timeline / pool format.
        if "_timeline" in val or "relationPool" in val:
            continue
        repo.upsert_value(
            db,
            page_id=entry.id,
            schema_id=schema_id,
            value={"_timeline": {"": val}},
        )
