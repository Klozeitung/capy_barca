"""kinship siblings

Revision ID: d6g8f7h90036
Revises: c5f7e6g80035
Create Date: 2026-11-07

Brings the family set (see ``app.blocks.kinship``) to the rules of 0.18.2.0:

1. Half-siblings is no longer a role. A property marked ``half_siblings``
   loses its marker and stays as an ordinary property, to be deleted by hand.
2. A select property marked ``sex`` points ``config.kinMaleOptionId`` at its
   option labelled male in either language, when it has one other than the
   female option.
3. Every marked relation (parents, children, partners, siblings) carries
   nuances: ``config.nuance.enabled`` is switched on.
4. Timelines: in a database with a family tree view every marked relation
   has one; elsewhere parents, children and siblings have one as soon as one
   of them has. A relation switched on here has its stored values wrapped
   into an always-valid timeline slot.
5. The links of the former half-siblings property are written into the
   siblings property of the same database, on both sides, as manual entries.
   A link carries the nuance it had when that was an alias of the half
   siblings group, else the base nuance ``kin.sibling.half``. A pair already
   linked as siblings keeps its link and gains the nuance only where it has
   none. Without a marked siblings property nothing is written.
6. Aliases: the half siblings group merges into siblings with the base
   ``kin.sibling.half``; a siblings alias without base gets
   ``kin.sibling.biological``. A moved alias whose label the siblings group
   has already is merged into that nuance, and values follow it.

The rules are copied here on purpose: a migration must keep doing what it did
when it was written, whatever the application code later becomes.

Downgrade
---------
Removes the male marker. Values, nuances, timelines and aliases stay as they
are.
"""
import json
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d6g8f7h90036"
down_revision: Union[str, None] = "c5f7e6g80035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLE_KEY = "kinRole"
FEMALE_OPTION_KEY = "kinFemaleOptionId"
MALE_OPTION_KEY = "kinMaleOptionId"
MALE_LABELS = ("männlich", "male")
RELATION_ROLES = ("parents", "children", "partners", "siblings")
COUPLED_ROLES = ("parents", "children", "siblings")
HALF_SIBLINGS = "half_siblings"
FAMILY_TREE_VIEW = "family_tree"

ALIASES_KEY = "kinship_nuances"
SIBLING_HALF = "kin.sibling.half"
SIBLING_BIOLOGICAL = "kin.sibling.biological"
SIBLING_BASE_LABELS = {
    "kin.sibling.biological": ("leiblich", "biological"),
    "kin.sibling.half": ("halb", "half"),
    "kin.sibling.putative": ("vorgeblich", "putative"),
    "kin.sibling.legal": ("rechtlich", "legal"),
    "kin.sibling.adoptive": ("adoptiv", "adoptive"),
    "kin.sibling.foster": ("Pflege", "foster"),
    "kin.sibling.step": ("Stief", "step"),
}


def _fold(text: Any) -> str:
    return text.strip().casefold() if isinstance(text, str) else ""


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


# ─── Timeline slots ──────────────────────────────────────────────────────────


def _parse_range(range_str: str) -> tuple[Optional[str], Optional[str]]:
    if "→" in range_str:
        start, end = range_str.split("→", 1)
        return start.strip() or None, end.strip() or None
    return range_str.strip() or None, None


def pool_to_timeline(pool: dict, nuance_pool: Optional[dict] = None) -> dict:
    """
    The ``_timeline`` slots of a relation pool, as the application derived
    them when this migration was written (minute resolution, an end covers
    its minute, always-valid links merged into every slot).
    """
    if not pool:
        return {}
    minute = timedelta(minutes=1)
    np_map: dict = nuance_pool or {}

    def nuance_for(uid: str, range_str: str) -> Optional[str]:
        ranges = np_map.get(uid)
        if not isinstance(ranges, dict):
            return None
        return ranges.get(range_str) or None

    def parse_ts(ts: str) -> Optional[datetime]:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            return None
        return dt.replace(tzinfo=None, second=0, microsecond=0)

    def fmt(dt: datetime) -> str:
        return (
            f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
            f"T{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
        )

    always_uids: list[str] = []
    always_nuance: dict[str, str] = {}
    parsed: list[tuple[str, Optional[datetime], Optional[datetime], str]] = []
    for uid, uid_ranges in pool.items():
        for r in uid_ranges or []:
            if r == "":
                if uid not in always_uids:
                    always_uids.append(uid)
                label = nuance_for(uid, "")
                if label and uid not in always_nuance:
                    always_nuance[uid] = label
                continue
            s_raw, e_raw = _parse_range(r)
            start = parse_ts(s_raw) if s_raw is not None else None
            if s_raw is not None and start is None:
                continue
            end_exclusive: Optional[datetime] = None
            if e_raw is not None:
                end = parse_ts(e_raw)
                if end is None:
                    continue
                try:
                    end_exclusive = end + minute
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
        related = always_uids + [u for u in bounded if u not in always_uids]
        nuances = dict(always_nuance)
        for uid, r in pairs:
            if uid in nuances:
                continue
            label = nuance_for(uid, r)
            if label:
                nuances[uid] = label
        slot: dict = {"related_ids": related}
        if nuances:
            slot["nuances"] = nuances
        return slot

    def always_only() -> dict:
        if not always_uids:
            return {}
        slot: dict = {"related_ids": list(always_uids)}
        if always_nuance:
            slot["nuances"] = dict(always_nuance)
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
    if any(s is None for _uid, s, _e, _r in parsed) or always_uids:
        first = ordered[0]
        try:
            before_end: Optional[datetime] = first - minute
        except OverflowError:
            before_end = None
        if before_end is not None:
            slot = build_slot([
                (uid, r) for uid, s, e, r in parsed if s is None and (e is None or e >= first)
            ])
            if slot["related_ids"]:
                timeline[f"→{fmt(before_end)}"] = slot
    for i, point in enumerate(ordered):
        pairs = [
            (uid, r) for uid, s, e, r in parsed
            if (s is None or s <= point) and (e is None or point < e)
        ]
        slot = build_slot(pairs)
        if not slot["related_ids"]:
            continue
        if i < len(ordered) - 1:
            key = f"{fmt(point)}→{fmt(ordered[i + 1] - minute)}"
        else:
            active = {u for u, _ in pairs}
            still_open = bool(always_uids) or any(
                e is None for uid, s, e, _r in parsed
                if uid in active and (s is None or s <= point)
            )
            key = f"{fmt(point)}→" if still_open else f"{fmt(point)}→{fmt(point)}"
        timeline[key] = slot
    return timeline


# ─── Rules ───────────────────────────────────────────────────────────────────


def male_option(config: dict) -> Optional[str]:
    """The option a ``sex`` property means male by, never the female one."""
    female = config.get(FEMALE_OPTION_KEY)
    folded = {_fold(label) for label in MALE_LABELS}
    for option in config.get("options") or []:
        if (
            isinstance(option, dict)
            and isinstance(option.get("id"), str)
            and option["id"] != female
            and _fold(option.get("label")) in folded
        ):
            return option["id"]
    return None


def with_nuance_on(config: dict) -> dict:
    nuance = config.get("nuance")
    nuance = dict(nuance) if isinstance(nuance, dict) else {}
    if nuance.get("enabled") is True:
        return config
    nuance["enabled"] = True
    return {**config, "nuance": nuance}


def timeline_roles(roles: set[str], timelined: set[str], has_tree: bool) -> set[str]:
    """
    The roles of a database that need a timeline, given the *roles* it
    holds and the ones *timelined* already.
    """
    if has_tree:
        return roles & set(RELATION_ROLES)
    if timelined & set(COUPLED_ROLES):
        return roles & set(COUPLED_ROLES)
    return set()


def to_pool(value: Any) -> Any:
    """A flat relation value wrapped into an always-valid timeline slot."""
    if not isinstance(value, dict) or "relationPool" in value or "_timeline" in value:
        return value
    related = [u for u in value.get("related_ids") or [] if isinstance(u, str)]
    if not related:
        return value
    nuances = value.get("nuances") if isinstance(value.get("nuances"), dict) else {}
    pool = {uid: [""] for uid in related}
    nuance_pool = {
        uid: {"": nuances[uid]} for uid in related
        if isinstance(nuances.get(uid), str) and nuances[uid].strip()
    }
    result: dict = {"relationPool": pool}
    if nuance_pool:
        result["nuancePool"] = nuance_pool
    result["_timeline"] = pool_to_timeline(pool, nuance_pool)
    return result


def links_of(value: Any) -> dict[str, list[tuple[str, Optional[str]]]]:
    """Linked entry to its ``(range, nuance)`` pairs, from either format."""
    result: dict[str, list[tuple[str, Optional[str]]]] = {}
    if not isinstance(value, dict):
        return result
    if isinstance(value.get("relationPool"), dict):
        nuance_pool = value.get("nuancePool") if isinstance(value.get("nuancePool"), dict) else {}
        for uid, ranges in value["relationPool"].items():
            labels = nuance_pool.get(uid) if isinstance(nuance_pool.get(uid), dict) else {}
            for r in ranges or []:
                result.setdefault(uid, []).append((r, labels.get(r)))
        return result
    nuances = value.get("nuances") if isinstance(value.get("nuances"), dict) else {}
    for uid in value.get("related_ids") or []:
        if isinstance(uid, str):
            result.setdefault(uid, []).append(("", nuances.get(uid)))
    return result


def add_link(value: Any, other: str, range_str: str, nuance: str, timelined: bool) -> Any:
    """
    *value* of a siblings property with a link to *other* for *range_str*.
    An existing link keeps its nuance; *nuance* fills only a missing one.
    """
    value = dict(value) if isinstance(value, dict) else {}
    if timelined:
        value = to_pool(value) if value.get("related_ids") else value
        pool = {k: list(v) for k, v in (value.get("relationPool") or {}).items()}
        nuance_pool = {k: dict(v) for k, v in (value.get("nuancePool") or {}).items()}
        ranges = pool.setdefault(other, [])
        if range_str not in ranges:
            ranges.append(range_str)
        if not nuance_pool.get(other, {}).get(range_str):
            nuance_pool.setdefault(other, {})[range_str] = nuance
        result: dict = {k: v for k, v in value.items() if k not in ("relationPool", "nuancePool", "_timeline")}
        result["relationPool"] = pool
        if nuance_pool:
            result["nuancePool"] = nuance_pool
        result["_timeline"] = pool_to_timeline(pool, nuance_pool)
        return result
    related = list(value.get("related_ids") or [])
    nuances = dict(value.get("nuances") or {})
    if other not in related:
        related.append(other)
    if not nuances.get(other):
        nuances[other] = nuance
    return {**value, "related_ids": related, "nuances": nuances}


def merge_aliases(aliases: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """
    The aliases after the half siblings group merged into siblings, and the
    ids of merged aliases mapped to the nuance that took them over.
    """
    taken: dict[str, str] = {
        _fold(label): base for base, labels in SIBLING_BASE_LABELS.items() for label in labels
    }
    for alias in aliases:
        if alias.get("group") == "siblings":
            taken.setdefault(_fold(alias.get("label")), alias.get("id"))
    result: list[dict] = []
    merged: dict[str, str] = {}
    for alias in aliases:
        group = alias.get("group")
        if group == "siblings":
            base = alias.get("base") if alias.get("base") in SIBLING_BASE_LABELS else SIBLING_BIOLOGICAL
            result.append({**alias, "base": base})
        elif group == HALF_SIBLINGS:
            existing = taken.get(_fold(alias.get("label")))
            if existing is not None and existing != alias.get("id"):
                merged[alias["id"]] = existing
                continue
            taken[_fold(alias.get("label"))] = alias.get("id")
            result.append({**alias, "group": "siblings", "base": SIBLING_HALF})
        else:
            result.append(alias)
    return result, merged


# ─── Database ────────────────────────────────────────────────────────────────


def _update_config(connection, schema_id, config: dict) -> None:
    connection.execute(sa.text(
        "UPDATE property_schemas SET config = CAST(:config AS jsonb) WHERE id = :id"
    ), {"id": schema_id, "config": json.dumps(config)})


def _values(connection, schema_id) -> list:
    return connection.execute(sa.text(
        "SELECT id, page_id, value FROM property_values "
        "WHERE property_schema_id = :schema AND value IS NOT NULL"
    ), {"schema": schema_id}).all()


def _write_value(connection, value_id, value) -> None:
    connection.execute(sa.text(
        "UPDATE property_values SET value = CAST(:value AS jsonb) WHERE id = :id"
    ), {"id": value_id, "value": json.dumps(value)})


def _upsert_value(connection, page_id, schema_id, value) -> None:
    existing = connection.execute(sa.text(
        "SELECT id FROM property_values WHERE page_id = :page AND property_schema_id = :schema"
    ), {"page": page_id, "schema": schema_id}).first()
    if existing is not None:
        _write_value(connection, existing.id, value)
        return
    connection.execute(sa.text(
        "INSERT INTO property_values (id, page_id, property_schema_id, value) "
        "VALUES (gen_random_uuid(), :page, :schema, CAST(:value AS jsonb))"
    ), {"page": page_id, "schema": schema_id, "value": json.dumps(value)})


def _replace_nuance(value: Any, mapping: dict[str, str]) -> Any:
    if not isinstance(value, dict) or not mapping:
        return value
    result = dict(value)
    if isinstance(result.get("nuances"), dict):
        result["nuances"] = {u: mapping.get(n, n) for u, n in result["nuances"].items()}
    if isinstance(result.get("nuancePool"), dict):
        result["nuancePool"] = {
            u: {r: mapping.get(n, n) for r, n in ranges.items()} if isinstance(ranges, dict) else ranges
            for u, ranges in result["nuancePool"].items()
        }
    if isinstance(result.get("_timeline"), dict):
        result["_timeline"] = {
            k: {**slot, "nuances": {u: mapping.get(n, n) for u, n in slot["nuances"].items()}}
            if isinstance(slot, dict) and isinstance(slot.get("nuances"), dict) else slot
            for k, slot in result["_timeline"].items()
        }
    return result


def migrate(connection: sa.engine.Connection) -> dict:
    """Apply the rules above. Returns a summary for tests."""
    summary: dict = {"unmarked": [], "timelined": [], "links": 0, "merged": {}}

    row = connection.execute(sa.text(
        "SELECT value FROM app_settings WHERE key = :key"
    ), {"key": ALIASES_KEY}).first()
    stored = _load(row.value) if row is not None else None
    aliases = (stored or {}).get("aliases") if isinstance(stored, dict) else None
    aliases = [a for a in aliases if isinstance(a, dict)] if isinstance(aliases, list) else []
    new_aliases, merged = merge_aliases(aliases)
    summary["merged"] = merged
    if row is not None and new_aliases != aliases:
        connection.execute(sa.text(
            "UPDATE app_settings SET value = CAST(:value AS jsonb) WHERE key = :key"
        ), {"key": ALIASES_KEY, "value": json.dumps({**(stored or {}), "aliases": new_aliases})})
    sibling_alias_ids = {a["id"] for a in new_aliases if a.get("group") == "siblings"}

    trees = {
        str(r.database_id) for r in connection.execute(sa.text(
            "SELECT DISTINCT database_id FROM database_views WHERE view_type = :type"
        ), {"type": FAMILY_TREE_VIEW}).all()
    }
    schemas = [
        {"id": r.id, "database_id": str(r.database_id), "config": dict(_load(r.config) or {})}
        for r in connection.execute(sa.text(
            "SELECT id, database_id, config FROM property_schemas WHERE config ? :key"
        ), {"key": ROLE_KEY}).all()
    ]

    by_database: dict[str, dict[str, dict]] = {}
    half_schemas: list[dict] = []
    for schema in schemas:
        config = schema["config"]
        role = config.get(ROLE_KEY)
        if role == HALF_SIBLINGS:
            config = {k: v for k, v in config.items() if k != ROLE_KEY}
            _update_config(connection, schema["id"], config)
            summary["unmarked"].append(str(schema["id"]))
            half_schemas.append(schema)
            continue
        changed = config
        if role == "sex":
            male = male_option(config)
            if male and config.get(MALE_OPTION_KEY) != male:
                changed = {**config, MALE_OPTION_KEY: male}
        if role in RELATION_ROLES:
            changed = with_nuance_on(changed)
        if changed is not config:
            _update_config(connection, schema["id"], changed)
            schema["config"] = changed
        by_database.setdefault(schema["database_id"], {})[role] = schema

    for database_id, roles in by_database.items():
        timelined = {r for r, s in roles.items() if s["config"].get("hasTimeline")}
        for role in timeline_roles(set(roles), timelined, database_id in trees) - timelined:
            schema = roles[role]
            schema["config"] = {**schema["config"], "hasTimeline": True}
            _update_config(connection, schema["id"], schema["config"])
            for pv in _values(connection, schema["id"]):
                before = _load(pv.value)
                after = to_pool(before)
                if after != before:
                    _write_value(connection, pv.id, after)
            summary["timelined"].append(str(schema["id"]))

    for half in half_schemas:
        siblings = by_database.get(half["database_id"], {}).get("siblings")
        values = _values(connection, half["id"])
        if merged:
            for pv in values:
                before = _load(pv.value)
                after = _replace_nuance(before, merged)
                if after != before:
                    _write_value(connection, pv.id, after)
        if siblings is None:
            continue
        timelined = bool(siblings["config"].get("hasTimeline"))
        for pv in values:
            page = str(pv.page_id)
            for other, pairs in links_of(_replace_nuance(_load(pv.value), merged)).items():
                for range_str, nuance in pairs:
                    if nuance not in sibling_alias_ids:
                        nuance = SIBLING_HALF
                    if not timelined:
                        range_str = ""
                    for side, target in ((page, other), (other, page)):
                        current = connection.scalar(sa.text(
                            "SELECT value FROM property_values "
                            "WHERE page_id = :page AND property_schema_id = :schema"
                        ), {"page": side, "schema": siblings["id"]})
                        current = _load(current)
                        pooled = timelined or (isinstance(current, dict) and "relationPool" in current)
                        updated = add_link(current, target, range_str, nuance, pooled)
                        if updated != current:
                            _upsert_value(connection, side, siblings["id"], updated)
                    summary["links"] += 1
    return summary


def upgrade() -> None:
    migrate(op.get_bind())


def downgrade() -> None:
    op.execute(
        "UPDATE property_schemas "
        f"SET config = config - '{MALE_OPTION_KEY}' "
        f"WHERE config ? '{MALE_OPTION_KEY}'"
    )
