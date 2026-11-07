"""Tests for reading the family graph and for the scope of a tree."""
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.blocks import kinship
from app.family_tree import graph as graph_module
from app.family_tree.graph import date_of, earliest, minute, scope, segments_of


@pytest.mark.parametrize("raw, expected", [
    ("2000-07-28", "2000-07-28T00:00"),
    ("2000-07-28T08:05:59", "2000-07-28T08:05"),
    ("0044-03-15T00:00:00Z", "0044-03-15T00:00"),
    ("", None),
    ("not a date", None),
    (None, None),
])
def test_minute_normalises_instants(raw, expected):
    assert minute(raw) == expected


def test_earliest_ignores_unknown_instants():
    assert earliest(None, "2045-07-17T00:00", "2000-07-28T00:00") == "2000-07-28T00:00"
    assert earliest(None, None) is None


def test_segments_of_reads_flat_and_pooled_values():
    aliases = [{"id": "a-1", "group": "parents", "base": "kin.parent.legal", "label": "Label A"}]
    flat = segments_of({"related_ids": ["x"], "nuances": {"x": "a-1"}}, aliases)
    assert flat["x"][0].base == "kin.parent.legal"
    assert flat["x"][0].start is None and flat["x"][0].range_key == ""

    pooled = segments_of({
        "relationPool": {"x": ["2000-07-28T00:00:00→2045-07-17T12:00:00"]},
        "nuancePool": {"x": {"2000-07-28T00:00:00→2045-07-17T12:00:00": "kin.parent.step"}},
    }, aliases)
    segment = pooled["x"][0]
    assert (segment.start, segment.end, segment.base) == ("2000-07-28T00:00", "2045-07-17T12:00", "kin.parent.step")
    assert segment.covers("2010-01-01T00:00") and not segment.covers("2045-07-17T12:01")


def test_date_of_reads_dates_and_computed_results():
    zone = ZoneInfo("Europe/Berlin")
    date = SimpleNamespace(type="date")
    assert date_of(date, {"start": "2000-07-28"}, zone) == "2000-07-28"
    assert date_of(date, {"start": "2000-07-28T10:00:00Z"}, zone).startswith("2000-07-28T12:00")
    rollup = SimpleNamespace(type="rollup")
    assert date_of(rollup, {"result": [{"start": "2045-07-17"}]}, zone) == "2045-07-17"
    assert date_of(rollup, {"result": None}, zone) is None


def _three_generations(family):
    family.people_named("Person A", "Person B", "Person C", "Person D", "Person E", "Person F", "Person G")
    # A and B are the parents of C and D; C and E are the parents of F; G is
    # the partner of E without children.
    for child in ("Person C", "Person D"):
        family.parent(child, "Person A")
        family.parent(child, "Person B")
    family.parent("Person F", "Person C")
    family.parent("Person F", "Person E")
    family.partner("Person E", "Person G")
    return family.build()


def test_lineage_holds_ancestors_descendants_and_other_parents(family):
    graph = _three_generations(family)
    assert scope(graph, "Person C") == {"Person A", "Person B", "Person C", "Person E", "Person F"}


def test_collateral_adds_the_lines_of_the_ancestors(family):
    graph = _three_generations(family)
    family.sibling("Person C", "Person D")
    assert "Person D" in scope(graph, "Person F", "collateral")
    assert "Person D" not in scope(graph, "Person F")


def test_connected_follows_partners_too(family):
    graph = _three_generations(family)
    assert "Person G" in scope(graph, "Person C", "connected")


def test_blood_skips_other_parents_and_adoptive_lines(family):
    family.people_named("Person A", "Person B", "Person C", "Person D")
    family.parent("Person B", "Person A")
    family.parent("Person C", "Person B")
    family.parent("Person C", "Person D")
    family.parent("Person D", "Person A", nuance="kin.parent.adoptive")
    graph = family.build()
    assert scope(graph, "Person B", "blood") == {"Person A", "Person B", "Person C"}


def test_an_unknown_root_has_no_scope(family):
    assert scope(family.build(), "Person X") == set()


def test_order_key_sorts_by_birth_then_position(family):
    family.person("Person A", birth="2045-07-17")
    family.person("Person B", birth="2000-07-28")
    family.person("Person C")
    graph = family.build()
    assert sorted(graph.people, key=graph.order_key) == ["Person B", "Person A", "Person C"]


def test_scopes_match_the_view_settings():
    from app.blocks.views import ViewSettings

    allowed = ViewSettings.model_fields["familyTreeScope"].annotation.__args__[0].__args__
    assert set(allowed) == set(graph_module.SCOPES)
    assert kinship.FAMILY_TREE_VIEW == "family_tree"
