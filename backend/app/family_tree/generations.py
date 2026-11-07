"""
Generations of a family tree: which row each person is drawn in.

Rules
-----
* The root is generation 0.
* Every child sits one generation below its anchor parent, as a fixed
  condition. The anchor is the mother (a biological parent marked female);
  without a known mother it is the parent of the strongest link, ranked
  biological, putative, legal, adoptive, foster, step, unspecified, and the
  first among equals. "First" goes by date of birth, then by the position
  of the entry: a relation with a timeline keeps no order of its links. The anchor links form a forest, so the
  fixed conditions never contradict each other.
* Other parents, partners and siblings are soft conditions: a parent one row
  above, partners and siblings in the same row. Where they disagree, the
  condition met first walking outwards from the root wins.
* A cycle of parent links is broken: the link that closes it is drawn but
  places nobody (see ``conflicts.parent_cycles``). A child with two
  biological mothers is anchored at the first of them and carries the
  warning ``two_mothers``, likewise ``two_fathers``.
* Siblings entered by hand who share no parent get a virtual parent, one per
  group of such siblings, one row above the highest of them. It exists only
  in the drawing.

Order within a row is left to the frontend.
"""
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from app.blocks import kinship
from app.family_tree import conflicts
from app.family_tree.graph import FamilyGraph

VIRTUAL_PREFIX = "virtual:"

_RANK = {nuance: index for index, nuance in enumerate(kinship.PARENT_RANK)}
UNSPECIFIED_RANK = len(kinship.PARENT_RANK)


def rank(base: Optional[str]) -> int:
    """The rank of a parent nuance, 0 the strongest; no nuance ranks last."""
    return _RANK.get(base, UNSPECIFIED_RANK)


@dataclass
class VirtualParent:
    id: str
    children: list[str]
    generation: int


@dataclass
class Placement:
    generation: dict[str, int]
    anchor: dict[str, str]
    broken: set[tuple[str, str]]
    warnings: dict[str, list[str]] = field(default_factory=dict)
    virtual: list[VirtualParent] = field(default_factory=list)


def _anchor(graph: FamilyGraph, child: str, members: set[str], broken: set) -> tuple[Optional[str], list[str]]:
    parents = [
        p for p in graph.people[child].parents
        if p in members and (p, child) not in broken
    ]
    if not parents:
        return None, []
    warnings = []
    parents.sort(key=graph.order_key)
    bio = [p for p in conflicts.biological_parents(graph, child) if p in parents]
    mothers = [p for p in bio if graph.people[p].sex == "female"]
    fathers = [p for p in bio if graph.people[p].sex == "male"]
    if len(mothers) > 1:
        warnings.append("two_mothers")
    if len(fathers) > 1:
        warnings.append("two_fathers")
    if mothers:
        return mothers[0], warnings
    best = min(
        parents,
        key=lambda p: (min(rank(s.base) for s in graph.people[child].parents[p]), parents.index(p)),
    )
    return best, warnings


def _distances(graph: FamilyGraph, root: str, members: set[str]) -> list[str]:
    """*members* in the order a walk outwards from *root* reaches them."""
    order = [root]
    seen = {root}
    queue = deque([root])
    while queue:
        current = queue.popleft()
        for nxt in _neighbours(graph, current, members):
            if nxt[0] not in seen:
                seen.add(nxt[0])
                order.append(nxt[0])
                queue.append(nxt[0])
    return order


def _neighbours(graph: FamilyGraph, person_id: str, members: set[str]) -> list[tuple[str, int]]:
    """Members linked to *person_id*, with their row relative to it."""
    person = graph.people[person_id]
    result = [(p, -1) for p in person.parents if p in members]
    result += [(c, 1) for c in graph.children_of(person_id) if c in members]
    result += [(p, 0) for p in person.partners if p in members]
    result += [(s, 0) for s in person.siblings if s in members]
    return result


def place(graph: FamilyGraph, root: str, members: set[str]) -> Placement:
    """The generations of *members* around *root* (see the module)."""
    members = set(members) | {root}
    order = _distances(graph, root, members)
    order += sorted(members - set(order), key=graph.order_key)
    _cycles, broken = conflicts.parent_cycles(graph, members, order)

    anchor: dict[str, str] = {}
    warnings: dict[str, list[str]] = {}
    for person_id in members:
        parent, person_warnings = _anchor(graph, person_id, members, broken)
        if parent is not None:
            anchor[person_id] = parent
        if person_warnings:
            warnings[person_id] = person_warnings

    def top_and_depth(person_id: str) -> tuple[str, int]:
        depth = 0
        while person_id in anchor:
            person_id = anchor[person_id]
            depth += 1
        return person_id, depth

    tops = {p: top_and_depth(p) for p in members}
    top_generation: dict[str, int] = {}
    generation: dict[str, int] = {}

    def settle(person_id: str, wanted: int) -> None:
        top, depth = tops[person_id]
        if top not in top_generation:
            top_generation[top] = wanted - depth

    for start in order:
        if tops[start][0] in top_generation:
            continue
        settle(start, 0)
        queue = deque([start])
        visited = {start}
        while queue:
            current = queue.popleft()
            top, depth = tops[current]
            here = top_generation[top] + depth
            for nxt, offset in _neighbours(graph, current, members):
                if (nxt, current) in broken or (current, nxt) in broken:
                    continue
                settle(nxt, here + offset)
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)

    for person_id in members:
        top, depth = tops[person_id]
        generation[person_id] = top_generation[top] + depth

    placement = Placement(generation, anchor, broken, warnings)
    placement.virtual = _virtual_parents(graph, members, generation)
    return placement


def _virtual_parents(graph: FamilyGraph, members: set[str], generation: dict[str, int]) -> list[VirtualParent]:
    """One virtual parent per group of members who are siblings without a shared parent."""
    links: dict[str, set[str]] = {}
    for person_id in members:
        person = graph.people[person_id]
        for other in person.siblings:
            if other not in members or other == person_id:
                continue
            if set(person.parents) & set(graph.people[other].parents):
                continue
            links.setdefault(person_id, set()).add(other)
            links.setdefault(other, set()).add(person_id)
    result: list[VirtualParent] = []
    seen: set[str] = set()
    for person_id in sorted(links, key=graph.order_key):
        if person_id in seen:
            continue
        group: list[str] = []
        stack = [person_id]
        seen.add(person_id)
        while stack:
            current = stack.pop()
            group.append(current)
            for other in links.get(current, ()):
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
        group.sort(key=graph.order_key)
        result.append(VirtualParent(
            id=f"{VIRTUAL_PREFIX}{group[0]}",
            children=group,
            generation=min(generation[g] for g in group) - 1,
        ))
    return result
