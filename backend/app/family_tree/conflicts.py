"""
Contradictions in the family data, and plausibility warnings.

The family tree view draws in spite of them (see ``app.family_tree.generations``);
the sibling mapper leaves every pair a contradiction touches as it is and
lists all contradictions after its run (see ``app.family_tree.siblings``).

Contradictions
--------------
``cycle``                    someone is their own ancestor; ``entries`` is
                             the cycle, each a child of the one before
``two_mothers``              a child with more than one biological mother;
                             ``entries`` is the child, then the mothers
``two_fathers``              the same for fathers
``too_many_parents``         more than two biological parents
``sibling_is_parent``        two siblings of whom one is the other's parent
``sibling_nuance_parents``   siblings entered by hand as biological or half
                             siblings whose known biological parents say
                             otherwise
``sibling_nuance_differs``   siblings entered by hand with a nuance that
                             differs from the one their parents give
                             (found by the mapper)

Warnings
--------
``parent_born_after_child``  a biological parent born after the child

A biological parent is one linked with the nuance ``biological`` (or an
alias of it). A mother is a biological parent marked female, a father one
marked male; a parent without sex counts as neither.
"""
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.blocks import kinship
from app.family_tree.graph import FamilyGraph

BIOLOGICAL = kinship.PARENT_BIOLOGICAL


@dataclass
class Finding:
    kind: str
    entries: list[str]

    def as_dict(self) -> dict:
        return {"kind": self.kind, "entries": list(self.entries)}


@dataclass
class Findings:
    conflicts: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    # People and pairs the mapper leaves as they are.
    blocked: set[str] = field(default_factory=set)
    blocked_pairs: set[frozenset] = field(default_factory=set)
    # Parent links that close a cycle, as (parent, child).
    back_edges: set[tuple[str, str]] = field(default_factory=set)

    def add_conflict(self, kind: str, entries: list[str], *, people=(), pair=None) -> None:
        self.conflicts.append(Finding(kind, entries))
        self.blocked |= set(people)
        if pair is not None:
            self.blocked_pairs.add(frozenset(pair))


def biological_parents(graph: FamilyGraph, person_id: str) -> list[str]:
    """
    The biological parents of *person_id*, by date of birth and position (a
    relation with a timeline keeps no order of its links).
    """
    person = graph.people.get(person_id)
    if person is None:
        return []
    return sorted(
        (
            p for p, segments in person.parents.items()
            if p in graph.people and any(s.base == BIOLOGICAL for s in segments)
        ),
        key=graph.order_key,
    )


def parent_cycles(
    graph: FamilyGraph, members: Iterable[str], start_order: Iterable[str] = (),
) -> tuple[list[list[str]], set[tuple[str, str]]]:
    """
    The cycles among the parent links of *members*, and the links that close
    them: a depth-first search from parent to child, started at the people
    of *start_order* first, marks every link to someone still on the path.
    Removing those links leaves no cycle.
    """
    member_set = set(members)
    order = [p for p in start_order if p in member_set]
    order += sorted(member_set - set(order))
    state: dict[str, int] = {}
    path: list[str] = []
    cycles: list[list[str]] = []
    back: set[tuple[str, str]] = set()

    def visit(node: str) -> None:
        # Iterative, so long lineages do not hit the recursion limit.
        stack = [(node, iter(c for c in graph.children_of(node) if c in member_set))]
        state[node] = 1
        path.append(node)
        while stack:
            current, children = stack[-1]
            child = next(children, None)
            if child is None:
                stack.pop()
                path.pop()
                state[current] = 2
                continue
            if state.get(child) == 1:
                back.add((current, child))
                cycles.append(path[path.index(child):])
            elif child not in state:
                state[child] = 1
                path.append(child)
                stack.append((child, iter(c for c in graph.children_of(child) if c in member_set)))

    for node in order:
        if node not in state:
            visit(node)
    return cycles, back


def find(graph: FamilyGraph, members: Iterable[str], start_order: Iterable[str] = ()) -> Findings:
    """Every contradiction and warning among *members* (see the module)."""
    member_set = set(members)
    findings = Findings()

    cycles, findings.back_edges = parent_cycles(graph, member_set, start_order)
    for cycle in cycles:
        findings.add_conflict("cycle", cycle, people=cycle)

    for person_id in sorted(member_set, key=graph.order_key):
        bio = biological_parents(graph, person_id)
        mothers = [p for p in bio if graph.people[p].sex == "female"]
        fathers = [p for p in bio if graph.people[p].sex == "male"]
        if len(mothers) > 1:
            findings.add_conflict("two_mothers", [person_id, *mothers], people=[person_id])
        if len(fathers) > 1:
            findings.add_conflict("two_fathers", [person_id, *fathers], people=[person_id])
        if len(bio) > 2 and len(mothers) <= 1 and len(fathers) <= 1:
            findings.add_conflict("too_many_parents", [person_id, *bio], people=[person_id])
        child = graph.people[person_id]
        for parent_id in bio:
            parent = graph.people[parent_id]
            if parent.birth and child.birth and parent.birth > child.birth:
                findings.warnings.append(Finding("parent_born_after_child", [parent_id, person_id]))

    seen_pairs: set[frozenset] = set()
    for person_id in sorted(member_set, key=graph.order_key):
        person = graph.people[person_id]
        for other_id, segments in person.siblings.items():
            if other_id not in graph.people:
                continue
            pair = frozenset((person_id, other_id))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            other = graph.people[other_id]
            if other_id in person.parents or person_id in other.parents:
                findings.add_conflict("sibling_is_parent", [person_id, other_id], pair=pair)
                continue
            derived = other_id in person.derived_siblings or person_id in other.derived_siblings
            if derived:
                continue
            bases = {s.base for s in segments} | {
                s.base for s in other.siblings.get(person_id, [])
            }
            if _contradicts_parents(graph, person_id, other_id, bases):
                findings.add_conflict("sibling_nuance_parents", [person_id, other_id], pair=pair)
    return findings


def _contradicts_parents(graph: FamilyGraph, a: str, b: str, bases: set[Optional[str]]) -> bool:
    """
    Whether siblings entered by hand as biological or half siblings
    contradict biological parents both of them have completely (two each).
    """
    bio_a, bio_b = set(biological_parents(graph, a)), set(biological_parents(graph, b))
    if len(bio_a) != 2 or len(bio_b) != 2:
        return False
    shared = len(bio_a & bio_b)
    if kinship.SIBLING_BIOLOGICAL in bases and shared != 2:
        return True
    return kinship.SIBLING_HALF in bases and shared != 1
