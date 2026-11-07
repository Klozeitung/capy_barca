"""
The sibling mapper: siblings derived from shared parents.

Run from the family tree view's settings (``POST …/family-tree/map``), the
mapper walks the people in the tree's scope and writes into the siblings
property of the family set every pair of children who share a parent. A
derived link is marked in the value's ``derived`` list (see
``app.blocks.timeline``); the chips look the same, a tooltip tells them
apart.

Nuance
------
For every shared parent the weaker of the two parent links counts (ranked
biological, putative, legal, adoptive, foster, step, unspecified). Two shared
biological parents make biological siblings. Exactly one makes half siblings
when the other biological parent of each is known and differs; otherwise the
pair is derived without nuance, since nothing confirms half or full siblings.
Without a shared biological parent the best remaining link decides:
putative, legal, adoptive, foster or step siblings, or no nuance for
unspecified links.

When the siblings property has a timeline, so do parents and children (the
timelines of the family set are coupled), and a pair is derived per period:
it is linked while both children are linked to a shared parent, with the
nuance of that period. Dates of birth and death do not count; they are never
written into a relation.

Existing links
--------------
* A derived link is rewritten to what the parents give now and removed once
  they no longer give it. Removing it by hand does not last; the next run
  brings it back.
* A link entered by hand stays manual. Without any nuance it gets the derived
  nuance; with a nuance that differs from the derived one it is reported as
  ``sibling_nuance_differs`` and left alone. A link changed by hand stops
  being derived when it is saved (``app.blocks.entry_service``).
* Pairs a contradiction touches (``app.family_tree.conflicts``) are left as
  they are; the mapper still maps everything else and lists every
  contradiction at the end.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.blocks import kinship
from app.blocks import repository as repo
from app.blocks.computed import SchemaIndex, recompute_after_value_change
from app.blocks.readonly_properties import refresh_last_edited
from app.blocks.timeline import DERIVED_KEY, pool_to_timeline, with_derived
from app.family_tree import conflicts as conflict_rules
from app.family_tree.generations import UNSPECIFIED_RANK, rank
from app.family_tree.graph import FamilyGraph, Segment

#: A derived pair: per period (range key) the sibling nuance, None for none.
Periods = dict[str, Optional[str]]

_NOT_SIBLINGS = object()


# ─── Minutes ─────────────────────────────────────────────────────────────────


def _parse(minute: str) -> datetime:
    return datetime.fromisoformat(minute)


def _format(moment: datetime) -> str:
    return (
        f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"
        f"T{moment.hour:02d}:{moment.minute:02d}"
    )


def next_minute(minute: str) -> Optional[str]:
    try:
        return _format(_parse(minute) + timedelta(minutes=1))
    except OverflowError:
        return None


def previous_minute(minute: str) -> Optional[str]:
    try:
        return _format(_parse(minute) - timedelta(minutes=1))
    except OverflowError:
        return None


def range_key(start: Optional[str], end: Optional[str]) -> str:
    """A pool range for *start* to *end* (inclusive minutes, None open)."""
    if start is None and end is None:
        return ""
    left = f"{start}:00" if start else ""
    right = f"{end}:00" if end else ""
    return f"{left}→{right}"


# ─── Deriving a pair ─────────────────────────────────────────────────────────


def _best(segments: list[Segment], at: Optional[str], timed: bool) -> Optional[int]:
    """The rank of the strongest segment holding at *at*, None when none does."""
    if timed:
        if at is None:
            active = [s for s in segments if s.start is None]
        else:
            active = [s for s in segments if s.covers(at)]
    else:
        active = segments
    return min((rank(s.base) for s in active), default=None)


def pair_nuance(
    graph: FamilyGraph, a: str, b: str, ranks: dict[str, tuple[int, int]],
) -> object:
    """
    The sibling nuance of *a* and *b* given the ranks of their links to each
    shared parent (``{parent: (rank of a's link, rank of b's link)}``), None
    for siblings without nuance, ``_NOT_SIBLINGS`` without a shared parent.
    """
    if not ranks:
        return _NOT_SIBLINGS
    weaker = {parent: max(pair) for parent, pair in ranks.items()}
    biological = rank(kinship.PARENT_BIOLOGICAL)
    shared_bio = [p for p, r in weaker.items() if r == biological]
    if len(shared_bio) >= 2:
        return kinship.SIBLING_BIOLOGICAL
    if len(shared_bio) == 1:
        others_a = set(conflict_rules.biological_parents(graph, a)) - set(shared_bio)
        others_b = set(conflict_rules.biological_parents(graph, b)) - set(shared_bio)
        if others_a and others_b and not (others_a & others_b):
            return kinship.SIBLING_HALF
        return None
    best = min(weaker.values())
    if best >= UNSPECIFIED_RANK:
        return None
    return kinship.SIBLING_FOR_PARENT[kinship.PARENT_RANK[best]]


def derive_pair(graph: FamilyGraph, a: str, b: str, timed: bool) -> Optional[Periods]:
    """
    The periods in which *a* and *b* are siblings through shared parents,
    with their nuance; None when they share no parent at any time.
    """
    links_a, links_b = graph.people[a].parents, graph.people[b].parents
    shared = [p for p in links_a if p in links_b and p in graph.people]
    if not shared:
        return None

    def at(instant: Optional[str]) -> object:
        ranks = {}
        for parent in shared:
            rank_a = _best(links_a[parent], instant, timed)
            rank_b = _best(links_b[parent], instant, timed)
            if rank_a is not None and rank_b is not None:
                ranks[parent] = (rank_a, rank_b)
        return pair_nuance(graph, a, b, ranks)

    if not timed:
        nuance = at(None)
        return None if nuance is _NOT_SIBLINGS else {"": nuance}

    points: set[str] = set()
    for parent in shared:
        for segment in (*links_a[parent], *links_b[parent]):
            if segment.start:
                points.add(segment.start)
            if segment.end:
                after = next_minute(segment.end)
                if after:
                    points.add(after)
    ordered = sorted(points)
    # Elementary periods: before the first point, then from each point to the next.
    starts: list[Optional[str]] = [None, *ordered]
    results = [at(start) for start in starts]

    periods: Periods = {}
    index = 0
    while index < len(starts):
        result = results[index]
        end_index = index
        while end_index + 1 < len(starts) and results[end_index + 1] == result:
            end_index += 1
        if result is not _NOT_SIBLINGS:
            start = starts[index]
            end = previous_minute(starts[end_index + 1]) if end_index + 1 < len(starts) else None
            periods[range_key(start, end)] = result
        index = end_index + 1
    return periods or None


# ─── Values ──────────────────────────────────────────────────────────────────


def _current_periods(segments: list[Segment]) -> Periods:
    return {s.range_key: s.nuance for s in segments}


def set_link(value: Optional[dict], other: str, periods: Periods, pooled: bool, derived: Optional[bool]) -> dict:
    """
    *value* of a siblings property with its link to *other* replaced by
    *periods*. *derived* True marks the link derived, False manual, None
    leaves the mark as it is.
    """
    value = dict(value) if isinstance(value, dict) else {}
    marks = [u for u in value.get(DERIVED_KEY) or [] if u != other]
    if derived or (derived is None and other in (value.get(DERIVED_KEY) or [])):
        marks.append(other)
    if pooled:
        pool = {k: list(v) for k, v in (value.get("relationPool") or {}).items()}
        nuance_pool = {k: dict(v) for k, v in (value.get("nuancePool") or {}).items()}
        pool[other] = list(periods)
        labels = {r: n for r, n in periods.items() if n}
        if labels:
            nuance_pool[other] = labels
        else:
            nuance_pool.pop(other, None)
        result = {k: v for k, v in value.items() if k not in ("relationPool", "nuancePool", "_timeline", "related_ids", "nuances")}
        result["relationPool"] = pool
        if nuance_pool:
            result["nuancePool"] = nuance_pool
        result["_timeline"] = pool_to_timeline(pool, nuance_pool)
    else:
        related = list(value.get("related_ids") or [])
        nuances = dict(value.get("nuances") or {})
        if other not in related:
            related.append(other)
        nuance = next(iter(periods.values()), None)
        if nuance:
            nuances[other] = nuance
        else:
            nuances.pop(other, None)
        result = {k: v for k, v in value.items() if k not in ("related_ids", "nuances")}
        result["related_ids"] = related
        if nuances:
            result["nuances"] = nuances
    return with_derived(result, marks)


def remove_link(value: Optional[dict], other: str) -> Optional[dict]:
    """*value* of a siblings property without its link to *other*."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if isinstance(result.get("relationPool"), dict):
        pool = {k: v for k, v in result["relationPool"].items() if k != other}
        nuance_pool = {k: v for k, v in (result.get("nuancePool") or {}).items() if k != other}
        result["relationPool"] = pool
        if nuance_pool:
            result["nuancePool"] = nuance_pool
        else:
            result.pop("nuancePool", None)
        result["_timeline"] = pool_to_timeline(pool, nuance_pool)
        if not pool:
            return None
    else:
        related = [u for u in result.get("related_ids") or [] if u != other]
        nuances = {u: n for u, n in (result.get("nuances") or {}).items() if u != other}
        if not related:
            return None
        result["related_ids"] = related
        if nuances:
            result["nuances"] = nuances
        else:
            result.pop("nuances", None)
    marks = [u for u in result.get(DERIVED_KEY) or [] if u != other]
    return with_derived(result, marks)


# ─── Running ─────────────────────────────────────────────────────────────────


@dataclass
class MapperResult:
    added: int = 0
    updated: int = 0
    removed: int = 0
    filled: int = 0
    conflicts: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    changed_entries: set[str] = field(default_factory=set)
    # Other databases whose computed values the run changed.
    affected_databases: set[uuid.UUID] = field(default_factory=set)


def _bases(periods: Periods, aliases: list[dict]) -> set[str]:
    return {kinship.base_of(n, aliases) for n in periods.values() if n}


def plan(graph: FamilyGraph, members: set[str], root: str, timed: bool) -> tuple[dict[str, Optional[dict]], MapperResult]:
    """
    What the mapper writes for *members*: the new siblings value per entry,
    and the result to report. Pure; :func:`run` stores the values.
    """
    findings = conflict_rules.find(graph, members, [root])
    result = MapperResult(
        conflicts=[f.as_dict() for f in findings.conflicts],
        warnings=[f.as_dict() for f in findings.warnings],
    )
    blocked, blocked_pairs = set(findings.blocked), set(findings.blocked_pairs)
    values: dict[str, Optional[dict]] = {
        person_id: graph.sibling_values.get(person_id) for person_id in graph.people
    }
    touched: set[str] = set()

    def derived_pair(a: str, b: str) -> bool:
        return b in graph.people[a].derived_siblings or a in graph.people[b].derived_siblings

    wanted: dict[frozenset, Periods] = {}
    for person_id in sorted(members, key=graph.order_key):
        if person_id in blocked:
            continue
        for parent in graph.parents_of(person_id):
            for other in graph.children_of(parent):
                pair = frozenset((person_id, other))
                if other == person_id or other in blocked or pair in blocked_pairs or pair in wanted:
                    continue
                person, sibling = graph.people[person_id], graph.people[other]
                if other in person.parents or person_id in sibling.parents:
                    result.conflicts.append({"kind": "sibling_is_parent", "entries": [person_id, other]})
                    blocked_pairs.add(pair)
                    continue
                periods = derive_pair(graph, person_id, other, timed)
                if periods is not None:
                    wanted[pair] = periods

    def write(a: str, b: str, periods: Periods, derived: Optional[bool]) -> None:
        values[a] = set_link(values[a], b, periods, timed, derived)
        values[b] = set_link(values[b], a, periods, timed, derived)
        touched.update((a, b))

    for pair, periods in sorted(wanted.items(), key=lambda item: sorted(item[0])):
        a, b = sorted(pair, key=graph.order_key)
        person, sibling = graph.people[a], graph.people[b]
        linked = b in person.siblings or a in sibling.siblings
        if not linked:
            write(a, b, periods, True)
            result.added += 1
            continue
        current = _current_periods(person.siblings.get(b) or sibling.siblings.get(a) or [])
        if derived_pair(a, b):
            if current != periods or a not in sibling.siblings or b not in person.siblings:
                write(a, b, periods, True)
                result.updated += 1
            continue
        if not any(current.values()):
            filled = _filled(current, periods)
            if filled is not None and filled != current:
                write(a, b, filled, False)
                result.filled += 1
            continue
        wanted_bases = _bases(periods, graph.aliases)
        if wanted_bases and _bases(current, graph.aliases) != wanted_bases:
            result.conflicts.append({"kind": "sibling_nuance_differs", "entries": [a, b]})

    for person_id in sorted(members, key=graph.order_key):
        if person_id in blocked:
            continue
        for other in sorted(graph.people[person_id].derived_siblings):
            pair = frozenset((person_id, other))
            if pair in wanted or other in blocked or pair in blocked_pairs:
                continue
            if values.get(person_id) is not None and other in (values[person_id].get(DERIVED_KEY) or []):
                values[person_id] = remove_link(values[person_id], other)
                touched.add(person_id)
                if other in graph.people:
                    values[other] = remove_link(values[other], person_id)
                    touched.add(other)
                result.removed += 1

    changed = {
        person_id: values[person_id] for person_id in touched
        if values[person_id] != graph.sibling_values.get(person_id)
    }
    result.changed_entries = set(changed)
    return changed, result


def _filled(current: Periods, periods: Periods) -> Optional[Periods]:
    """
    A manual link without nuance with the derived nuance: per period when
    both have the same periods, else the one nuance the derivation gives
    throughout; None when that is ambiguous or there is nothing to fill.
    """
    if set(current) == set(periods):
        return dict(periods)
    nuances = set(periods.values())
    if len(nuances) == 1:
        nuance = next(iter(nuances))
        if nuance:
            return {key: nuance for key in current}
    return None


def run(
    db: Session, graph: FamilyGraph, members: set[str], root: str, user_id: uuid.UUID,
) -> MapperResult:
    """
    Plan and store the siblings of *members*, bring what is computed from
    them up to date, and return the result. Does not commit.
    """
    siblings = graph.roles.get(kinship.SIBLINGS)
    if siblings is None:
        return MapperResult()
    timed = bool((siblings.config or {}).get("hasTimeline"))
    changed, result = plan(graph, members, root, timed)
    if not changed:
        return result
    for person_id, value in changed.items():
        repo.upsert_value(db, page_id=uuid.UUID(person_id), schema_id=siblings.id, value=value)
    index = SchemaIndex.load(db)
    for person_id in sorted(changed):
        entry_id = uuid.UUID(person_id)
        edited = refresh_last_edited(db, graph.database_id, entry_id, user_id)
        result.affected_databases.update(recompute_after_value_change(
            db, graph.database_id, entry_id, {siblings.id} | edited, index=index,
        ))
    result.affected_databases.discard(graph.database_id)
    return result
