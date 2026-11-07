"""
Shared fixture of the family tree tests: ``family`` builds a
:class:`FamilyGraph` in memory, so the rules are tested without a database.
People are named "Person A", "Person B" and so on; their ids are the names.
"""
import uuid

import pytest

from app.blocks import kinship
from app.blocks.timeline import parse_pool_range, pool_to_timeline, with_derived
from app.family_tree.graph import FamilyGraph, Person, Segment, minute

BIO = "kin.parent.biological"


class FamilyBuilder:
    def __init__(self) -> None:
        self.people: dict[str, Person] = {}
        self.aliases: list[dict] = []
        self.timed = False

    def person(self, name: str, sex=None, birth=None, death=None) -> str:
        self.people[name] = Person(
            id=name, title=name, icon=None, avatar=None, position=float(len(self.people)),
            birth=minute(birth), death=minute(death), birth_value=birth, death_value=death,
            sex=sex,
        )
        return name

    def people_named(self, *names: str) -> list[str]:
        return [self.person(n) for n in names]

    @staticmethod
    def _segment(nuance, range_key: str) -> Segment:
        start, end = parse_pool_range(range_key) if range_key else (None, None)
        return Segment(minute(start), minute(end), nuance, kinship.base_of(nuance, []), range_key)

    def parent(self, child: str, parent: str, nuance=BIO, range_key: str = "") -> None:
        self.people[child].parents.setdefault(parent, []).append(self._segment(nuance, range_key))

    def partner(self, a: str, b: str, nuance="kin.partner.marriage", range_key: str = "") -> None:
        self.people[a].partners.setdefault(b, []).append(self._segment(nuance, range_key))
        self.people[b].partners.setdefault(a, []).append(self._segment(nuance, range_key))

    def sibling(self, a: str, b: str, nuance=None, derived=False, range_key: str = "") -> None:
        for x, y in ((a, b), (b, a)):
            self.people[x].siblings.setdefault(y, []).append(self._segment(nuance, range_key))
            if derived:
                self.people[x].derived_siblings.add(y)

    def _sibling_value(self, person: Person):
        if not person.siblings:
            return None
        if self.timed:
            pool = {u: [s.range_key for s in segs] for u, segs in person.siblings.items()}
            nuance_pool = {
                u: {s.range_key: s.nuance for s in segs if s.nuance}
                for u, segs in person.siblings.items()
                if any(s.nuance for s in segs)
            }
            value = {"relationPool": pool}
            if nuance_pool:
                value["nuancePool"] = nuance_pool
            value["_timeline"] = pool_to_timeline(pool, nuance_pool)
        else:
            value = {"related_ids": list(person.siblings)}
            nuances = {u: segs[0].nuance for u, segs in person.siblings.items() if segs[0].nuance}
            if nuances:
                value["nuances"] = nuances
        return with_derived(value, person.derived_siblings)

    def build(self) -> FamilyGraph:
        graph = FamilyGraph(uuid.uuid4(), self.people, {}, self.aliases)
        for person in self.people.values():
            for parent in person.parents:
                graph.children.setdefault(parent, set()).add(person.id)
            graph.sibling_values[person.id] = self._sibling_value(person)
        return graph


@pytest.fixture
def family() -> FamilyBuilder:
    return FamilyBuilder()
