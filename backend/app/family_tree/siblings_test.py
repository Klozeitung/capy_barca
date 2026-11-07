"""Tests for the sibling mapper's rules; the database run is in router_test.py."""
import pytest

from app.blocks.timeline import pool_to_timeline
from app.family_tree import siblings
from app.family_tree.siblings import derive_pair, plan, range_key, remove_link, set_link

ALL = {"Person P", "Person Q", "Person R", "Person S", "Person A", "Person B", "Person C"}


def _parents(family, child, *links):
    for parent, nuance in links:
        family.parent(child, parent, nuance=nuance)


@pytest.fixture
def people(family):
    family.people_named(*sorted(ALL))
    return family


BIO = "kin.parent.biological"


@pytest.mark.parametrize("links_a, links_b, expected", [
    ([("Person P", BIO), ("Person Q", BIO)], [("Person P", BIO), ("Person Q", BIO)], "kin.sibling.biological"),
    ([("Person P", BIO), ("Person Q", BIO)], [("Person P", BIO), ("Person R", BIO)], "kin.sibling.half"),
    # The second parent of one is unknown: nothing confirms half siblings.
    ([("Person P", BIO)], [("Person P", BIO), ("Person R", BIO)], None),
    ([("Person P", BIO)], [("Person P", "kin.parent.adoptive")], "kin.sibling.adoptive"),
    ([("Person P", "kin.parent.step"), ("Person Q", "kin.parent.foster")],
     [("Person P", "kin.parent.legal"), ("Person Q", BIO)], "kin.sibling.foster"),
    ([("Person P", None)], [("Person P", BIO)], None),
])
def test_the_weaker_link_per_parent_and_the_best_parent_decide(people, links_a, links_b, expected):
    _parents(people, "Person A", *links_a)
    _parents(people, "Person B", *links_b)
    assert derive_pair(people.build(), "Person A", "Person B", timed=False) == {"": expected}


def test_children_without_a_shared_parent_are_no_siblings(people):
    _parents(people, "Person A", ("Person P", BIO))
    _parents(people, "Person B", ("Person Q", BIO))
    assert derive_pair(people.build(), "Person A", "Person B", timed=False) is None


def test_timed_pairs_follow_the_periods_of_the_parent_links(people):
    people.parent("Person A", "Person P")
    people.parent("Person B", "Person P", nuance="kin.parent.foster", range_key="2000-07-28T00:00:00→2010-12-31T23:59:00")
    people.parent("Person B", "Person P", nuance="kin.parent.adoptive", range_key="2011-01-01T00:00:00→")
    periods = derive_pair(people.build(), "Person A", "Person B", timed=True)
    assert periods == {
        "2000-07-28T00:00:00→2010-12-31T23:59:00": "kin.sibling.foster",
        "2011-01-01T00:00:00→": "kin.sibling.adoptive",
    }


def test_range_key_writes_pool_ranges():
    assert range_key(None, None) == ""
    assert range_key("2000-07-28T00:00", None) == "2000-07-28T00:00:00→"
    assert range_key(None, "2045-07-17T12:00") == "→2045-07-17T12:00:00"


def test_set_link_and_remove_link_keep_the_other_links():
    value = {"related_ids": ["Person X"], "nuances": {"Person X": "n-1"}}
    linked = set_link(value, "Person Y", {"": "kin.sibling.half"}, pooled=False, derived=True)
    assert linked == {
        "related_ids": ["Person X", "Person Y"],
        "nuances": {"Person X": "n-1", "Person Y": "kin.sibling.half"},
        "derived": ["Person Y"],
    }
    assert remove_link(linked, "Person Y") == value
    assert remove_link({"related_ids": ["Person Y"]}, "Person Y") is None

    pooled = set_link(None, "Person Y", {"2000-07-28T00:00:00→": "kin.sibling.step"}, pooled=True, derived=False)
    assert pooled["relationPool"] == {"Person Y": ["2000-07-28T00:00:00→"]}
    assert pooled["_timeline"] == pool_to_timeline(pooled["relationPool"], pooled["nuancePool"])
    assert "derived" not in pooled


def _two_children(people, nuance_b=BIO):
    _parents(people, "Person A", ("Person P", BIO), ("Person Q", BIO))
    _parents(people, "Person B", ("Person P", nuance_b), ("Person Q", BIO))


def test_plan_adds_derived_siblings_on_both_sides(people):
    _two_children(people)
    changed, result = plan(people.build(), ALL, "Person A", timed=False)
    assert result.added == 1
    assert changed["Person A"] == {
        "related_ids": ["Person B"], "nuances": {"Person B": "kin.sibling.biological"}, "derived": ["Person B"],
    }
    assert changed["Person B"]["derived"] == ["Person A"]


def test_plan_rewrites_a_derived_link_and_removes_a_stale_one(people):
    _two_children(people, nuance_b="kin.parent.adoptive")
    people.parent("Person B", "Person R")
    people.sibling("Person A", "Person B", nuance="kin.sibling.biological", derived=True)
    people.sibling("Person A", "Person C", nuance="kin.sibling.step", derived=True)
    changed, result = plan(people.build(), ALL, "Person A", timed=False)
    assert (result.updated, result.removed) == (1, 1)
    assert changed["Person A"]["nuances"] == {"Person B": "kin.sibling.half"}
    assert changed["Person C"] is None


def test_plan_fills_a_manual_link_without_nuance_and_keeps_it_manual(people):
    _two_children(people)
    people.sibling("Person A", "Person B")
    changed, result = plan(people.build(), ALL, "Person A", timed=False)
    assert result.filled == 1
    assert changed["Person A"] == {"related_ids": ["Person B"], "nuances": {"Person B": "kin.sibling.biological"}}


def test_plan_reports_a_manual_nuance_that_differs_and_leaves_it(people):
    _two_children(people)
    people.sibling("Person A", "Person B", nuance="kin.sibling.step")
    changed, result = plan(people.build(), ALL, "Person A", timed=False)
    assert changed == {}
    assert result.conflicts == [{"kind": "sibling_nuance_differs", "entries": ["Person A", "Person B"]}]


def test_plan_accepts_manual_half_siblings_the_parents_cannot_confirm(people):
    _parents(people, "Person A", ("Person P", BIO))
    _parents(people, "Person B", ("Person P", BIO), ("Person R", BIO))
    people.sibling("Person A", "Person B", nuance="kin.sibling.half")
    changed, result = plan(people.build(), ALL, "Person A", timed=False)
    assert changed == {} and result.conflicts == []


def test_plan_maps_around_contradictions_and_lists_them_all(people):
    people.people["Person P"].sex = "female"
    people.people["Person R"].sex = "female"
    _parents(people, "Person A", ("Person P", BIO), ("Person R", BIO))
    _parents(people, "Person B", ("Person P", BIO))
    _parents(people, "Person C", ("Person Q", BIO))
    _parents(people, "Person S", ("Person Q", BIO))
    people.parent("Person Q", "Person C")
    changed, result = plan(people.build(), ALL, "Person A", timed=False)
    kinds = sorted(c["kind"] for c in result.conflicts)
    assert kinds == ["cycle", "two_mothers"]
    # Person A is blocked; Person B, C and S are untouched for the same reasons.
    assert changed == {}


def test_plan_writes_pairs_no_contradiction_touches(people):
    people.people["Person P"].sex = "female"
    people.people["Person R"].sex = "female"
    _parents(people, "Person A", ("Person P", BIO), ("Person R", BIO))
    _parents(people, "Person B", ("Person Q", BIO))
    _parents(people, "Person C", ("Person Q", BIO))
    changed, result = plan(people.build(), ALL, "Person A", timed=False)
    assert [c["kind"] for c in result.conflicts] == ["two_mothers"]
    assert set(changed) == {"Person B", "Person C"}


def test_timed_plan_writes_pools(people):
    people.timed = True
    people.parent("Person A", "Person P")
    people.parent("Person B", "Person P", nuance="kin.parent.step", range_key="2000-07-28T00:00:00→")
    changed, _result = plan(people.build(), ALL, "Person A", timed=True)
    assert changed["Person A"]["relationPool"] == {"Person B": ["2000-07-28T00:00:00→"]}
    assert changed["Person A"]["nuancePool"] == {"Person B": {"2000-07-28T00:00:00→": "kin.sibling.step"}}
    assert changed["Person A"]["derived"] == ["Person B"]


def test_minutes_step_across_boundaries():
    assert siblings.next_minute("2000-12-31T23:59") == "2001-01-01T00:00"
    assert siblings.previous_minute("2001-01-01T00:00") == "2000-12-31T23:59"
