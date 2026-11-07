"""
The family graph of a database, read from its family set.

:func:`load_graph` reads every entry of a database together with the values
of the family set (``app.blocks.kinship``) in a few queries and turns them
into :class:`Person` objects with their parent, partner and sibling links.
Each link carries :class:`Segment` objects: the periods it holds, with the
nuance stored for each. A relation without timeline has one open segment.

Instants
--------
Every instant is a wall-clock minute ``YYYY-MM-DDTHH:MM`` with a zero-padded
year, so instants compare as strings, here and in the frontend. A date
without a time is midnight. Dates of birth and death are given in the zone of
the request, like every date value; timeline boundaries are wall-clock
times already (see ``app.blocks.timeline``).

Scope
-----
:func:`scope` collects the people a tree around a root shows:

``lineage``     ancestors and descendants of the root, and the other parents
                of every child among them (the default)
``collateral``  the lineage of every ancestor too, so siblings, cousins and
                their descendants, with their other parents and the
                siblings entered by hand
``connected``   everybody reachable over parent, partner and sibling links
``blood``       the root's ancestors over biological, putative or
                unspecified parent links, and all their descendants over the
                same links; no other parents
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timezones
from app.blocks import kinship
from app.blocks import repository as repo
from app.blocks.models import Block, PropertyValue
from app.blocks.timeline import derived_ids, parse_pool_range, slot_at

SCOPES = ("lineage", "collateral", "connected", "blood")
DEFAULT_SCOPE = "lineage"

#: Parent nuances that make a blood relation; no nuance counts as possible.
BLOOD_NUANCES = frozenset({"kin.parent.biological", "kin.parent.putative", None})

_EXCLUDED = frozenset({"entry_template"})


# ─── Instants ────────────────────────────────────────────────────────────────


def minute(value: Any) -> Optional[str]:
    """*value* (ISO date or timestamp) as a wall-clock minute, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (
        f"{parsed.year:04d}-{parsed.month:02d}-{parsed.day:02d}"
        f"T{parsed.hour:02d}:{parsed.minute:02d}"
    )


def earliest(*instants: Optional[str]) -> Optional[str]:
    """The earliest of the known *instants*, None when none is known."""
    known = [i for i in instants if i]
    return min(known) if known else None


# ─── Structures ──────────────────────────────────────────────────────────────


@dataclass
class Segment:
    """
    A period of a link. *start* and *end* are minutes, None for an open
    side; *end* is the last minute covered. *nuance* is the stored nuance
    id, *base* the base nuance it counts as. *range_key* is the period as
    the relation pool stores it (``""`` for always).
    """

    start: Optional[str]
    end: Optional[str]
    nuance: Optional[str] = None
    base: Optional[str] = None
    range_key: str = ""

    def covers(self, at: str) -> bool:
        return (self.start is None or self.start <= at) and (self.end is None or at <= self.end)


@dataclass
class Person:
    id: str
    title: Optional[str]
    icon: Optional[str]
    avatar: Optional[str]
    position: float = 0.0
    birth: Optional[str] = None
    death: Optional[str] = None
    birth_value: Optional[str] = None
    death_value: Optional[str] = None
    sex: Optional[str] = None
    parents: dict[str, list[Segment]] = field(default_factory=dict)
    partners: dict[str, list[Segment]] = field(default_factory=dict)
    siblings: dict[str, list[Segment]] = field(default_factory=dict)
    derived_siblings: set[str] = field(default_factory=set)


@dataclass
class FamilyGraph:
    database_id: uuid.UUID
    people: dict[str, Person]
    roles: dict[str, Any]
    aliases: list[dict]
    sibling_values: dict[str, Optional[dict]] = field(default_factory=dict)
    children: dict[str, set[str]] = field(default_factory=dict)

    def parents_of(self, person_id: str) -> list[str]:
        person = self.people.get(person_id)
        return [p for p in person.parents if p in self.people] if person else []

    def children_of(self, person_id: str) -> list[str]:
        return sorted(
            (c for c in self.children.get(person_id, ()) if c in self.people),
            key=self.order_key,
        )

    def order_key(self, person_id: str):
        person = self.people[person_id]
        return (person.birth or "9999", person.position, person_id)


# ─── Reading values ──────────────────────────────────────────────────────────


def segments_of(value: Any, aliases: list[dict]) -> dict[str, list[Segment]]:
    """Linked entry to its segments, from a flat or pooled relation value."""
    result: dict[str, list[Segment]] = {}
    if not isinstance(value, dict):
        return result
    if isinstance(value.get("relationPool"), dict):
        nuance_pool = value.get("nuancePool") if isinstance(value.get("nuancePool"), dict) else {}
        for uid, ranges in value["relationPool"].items():
            labels = nuance_pool.get(uid) if isinstance(nuance_pool.get(uid), dict) else {}
            for range_key in ranges or []:
                start_raw, end_raw = parse_pool_range(range_key)
                nuance = labels.get(range_key) or None
                result.setdefault(uid, []).append(Segment(
                    start=minute(start_raw), end=minute(end_raw), nuance=nuance,
                    base=kinship.base_of(nuance, aliases), range_key=range_key,
                ))
        return result
    nuances = value.get("nuances") if isinstance(value.get("nuances"), dict) else {}
    for uid in value.get("related_ids") or []:
        if isinstance(uid, str):
            nuance = nuances.get(uid) or None
            result[uid] = [Segment(None, None, nuance, kinship.base_of(nuance, aliases))]
    return result


def _first_date(value: Any) -> Optional[str]:
    """The first date a computed result holds, as stored."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _first_date(value.get("start"))
    if isinstance(value, list):
        for item in value:
            found = _first_date(item)
            if found:
                return found
    return None


def date_of(schema: Any, value: Any, zone) -> Optional[str]:
    """
    The date a birth or death property holds, as an ISO string in *zone*: a
    date value's start, or the first date of a rollup or formula result.
    """
    if schema is None or not isinstance(value, dict):
        return None
    if schema.type == "date":
        if "_timeline" in value:
            value = slot_at(value.get("_timeline") or {}) or {}
        local = timezones.date_value_to_local(value, zone)
        start = (local or {}).get("start") if isinstance(local, dict) else None
        return start if isinstance(start, str) and start else None
    found = _first_date(value.get("result"))
    if found and timezones.is_utc_time(found):
        found = timezones.time_to_local(found, zone)
    return found


def _sex_of(schema: Any, value: Any) -> Optional[str]:
    if schema is None or not isinstance(value, dict):
        return None
    if "_timeline" in value:
        value = slot_at(value.get("_timeline") or {}) or {}
    option = value.get("option")
    config = schema.config or {}
    if option and option == config.get(kinship.FEMALE_OPTION_KEY):
        return "female"
    if option and option == config.get(kinship.MALE_OPTION_KEY):
        return "male"
    return None


def load_graph(db: Session, database_id: uuid.UUID) -> FamilyGraph:
    """The family graph of *database_id*'s active entries."""
    roles = {
        kinship.role_of(s): s
        for s in repo.list_schemas(db, database_id)
        if kinship.role_of(s) is not None
    }
    aliases = kinship.load_aliases(db)
    zone = timezones.zone_of(db)
    blocks = repo.list_children(db, database_id, state="active", exclude_types=_EXCLUDED)
    people = {
        str(b.id): Person(
            id=str(b.id),
            title=(b.content or {}).get("title"),
            icon=b.icon,
            avatar=getattr(b, "avatar", None),
            position=b.position or 0.0,
        )
        for b in blocks
    }
    graph = FamilyGraph(database_id, people, roles, aliases)
    schema_ids = {s.id: role for role, s in roles.items()}
    if not schema_ids or not people:
        return graph

    rows = db.execute(
        select(PropertyValue.page_id, PropertyValue.property_schema_id, PropertyValue.value)
        .join(Block, Block.id == PropertyValue.page_id)
        .where(
            Block.parent_id == database_id,
            PropertyValue.property_schema_id.in_(list(schema_ids)),
        )
    ).all()
    children_links: dict[str, dict[str, list[Segment]]] = {}
    for page_id, schema_id, value in rows:
        person = people.get(str(page_id))
        if person is None:
            continue
        role = schema_ids[schema_id]
        if role == kinship.PARENTS:
            person.parents = segments_of(value, aliases)
        elif role == kinship.CHILDREN:
            children_links[person.id] = segments_of(value, aliases)
        elif role == kinship.PARTNERS:
            person.partners = segments_of(value, aliases)
        elif role == kinship.SIBLINGS:
            person.siblings = segments_of(value, aliases)
            person.derived_siblings = set(derived_ids(value))
            graph.sibling_values[person.id] = value
        elif role == kinship.BIRTH:
            person.birth_value = date_of(roles[role], value, zone)
            person.birth = minute(person.birth_value)
        elif role == kinship.DEATH:
            person.death_value = date_of(roles[role], value, zone)
            person.death = minute(person.death_value)
        elif role == kinship.SEX:
            person.sex = _sex_of(roles[role], value)

    # A link only the children side holds still counts; the parents side
    # wins where both hold one.
    for parent_id, links in children_links.items():
        for child_id, segments in links.items():
            child = people.get(child_id)
            if child is not None and parent_id not in child.parents:
                child.parents[parent_id] = segments
    for person in people.values():
        for parent_id in person.parents:
            graph.children.setdefault(parent_id, set()).add(person.id)
    return graph


# ─── Scope ───────────────────────────────────────────────────────────────────


def _closure(start: Iterable[str], step) -> set[str]:
    seen: set[str] = set()
    stack = list(start)
    while stack:
        current = stack.pop()
        for nxt in step(current):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _blood_parents(graph: FamilyGraph, person_id: str) -> list[str]:
    person = graph.people[person_id]
    return [
        p for p, segments in person.parents.items()
        if p in graph.people and any(s.base in BLOOD_NUANCES for s in segments)
    ]


def _blood_children(graph: FamilyGraph, person_id: str) -> list[str]:
    return [c for c in graph.children_of(person_id) if person_id in _blood_parents(graph, c)]


def scope(graph: FamilyGraph, root: str, mode: str = DEFAULT_SCOPE) -> set[str]:
    """The people a tree around *root* shows in *mode* (see the module)."""
    if root not in graph.people:
        return set()
    if mode == "connected":
        def neighbours(x: str) -> list[str]:
            person = graph.people[x]
            return [
                *graph.parents_of(x), *graph.children_of(x),
                *(p for p in person.partners if p in graph.people),
                *(s for s in person.siblings if s in graph.people),
            ]
        return _closure([root], neighbours) | {root}
    if mode == "blood":
        ancestors = _closure([root], lambda x: _blood_parents(graph, x)) | {root}
        return _closure(ancestors, lambda x: _blood_children(graph, x)) | ancestors

    ancestors = _closure([root], graph.parents_of)
    if mode == "collateral":
        tops = ancestors | {root}
        descendants = _closure(tops, graph.children_of)
        members = tops | descendants
        for person_id in list(members):
            members |= {s for s in graph.people[person_id].siblings if s in graph.people}
        lines = members
    else:
        descendants = _closure([root], graph.children_of)
        lines = ancestors | descendants | {root}
    result = set(lines)
    for person_id in lines - ancestors:
        for child in graph.children_of(person_id):
            if child in lines:
                result |= set(graph.parents_of(child))
    return result
