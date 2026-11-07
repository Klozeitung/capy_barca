"""Tests for contradictions and warnings in the family data."""
from app.family_tree import conflicts


def _kinds(findings):
    return [f.kind for f in findings.conflicts]


def test_a_cycle_is_reported_with_its_members_and_blocks_them(family):
    family.people_named("Person A", "Person B")
    family.parent("Person B", "Person A")
    family.parent("Person A", "Person B")
    findings = conflicts.find(family.build(), {"Person A", "Person B"}, ["Person A"])
    assert _kinds(findings) == ["cycle"]
    assert set(findings.conflicts[0].entries) == {"Person A", "Person B"}
    assert findings.blocked == {"Person A", "Person B"}
    assert len(findings.back_edges) == 1


def test_two_biological_mothers_or_fathers_are_contradictions(family):
    family.person("Person M", sex="female")
    family.person("Person N", sex="female")
    family.person("Person F", sex="male")
    family.person("Person G", sex="male")
    family.person("Person C")
    family.person("Person D")
    family.parent("Person C", "Person M")
    family.parent("Person C", "Person N")
    family.parent("Person D", "Person F")
    family.parent("Person D", "Person G")
    findings = conflicts.find(family.build(), {"Person C", "Person D"})
    assert sorted(_kinds(findings)) == ["two_fathers", "two_mothers"]
    assert findings.blocked == {"Person C", "Person D"}


def test_a_second_mother_by_adoption_is_no_contradiction(family):
    family.person("Person M", sex="female")
    family.person("Person N", sex="female")
    family.person("Person C")
    family.parent("Person C", "Person M")
    family.parent("Person C", "Person N", nuance="kin.parent.adoptive")
    assert conflicts.find(family.build(), {"Person C"}).conflicts == []


def test_more_than_two_biological_parents_without_sex_are_a_contradiction(family):
    family.people_named("Person A", "Person B", "Person D", "Person C")
    for parent in ("Person A", "Person B", "Person D"):
        family.parent("Person C", parent)
    assert _kinds(conflicts.find(family.build(), {"Person C"})) == ["too_many_parents"]


def test_a_sibling_who_is_a_parent_is_a_contradiction(family):
    family.people_named("Person A", "Person B")
    family.parent("Person B", "Person A")
    family.sibling("Person A", "Person B")
    findings = conflicts.find(family.build(), {"Person A", "Person B"})
    assert _kinds(findings) == ["sibling_is_parent"]
    assert findings.blocked_pairs == {frozenset(("Person A", "Person B"))}


def test_manual_biological_siblings_need_the_same_known_parents(family):
    family.people_named("Person P", "Person Q", "Person R", "Person A", "Person B")
    family.parent("Person A", "Person P")
    family.parent("Person A", "Person Q")
    family.parent("Person B", "Person P")
    family.parent("Person B", "Person R")
    family.sibling("Person A", "Person B", nuance="kin.sibling.biological")
    assert _kinds(conflicts.find(family.build(), {"Person A"})) == ["sibling_nuance_parents"]


def test_manual_half_siblings_are_fine_while_a_parent_is_unknown(family):
    family.people_named("Person P", "Person A", "Person B")
    family.parent("Person A", "Person P")
    family.parent("Person B", "Person P")
    family.sibling("Person A", "Person B", nuance="kin.sibling.half")
    assert conflicts.find(family.build(), {"Person A"}).conflicts == []


def test_a_parent_born_after_the_child_is_a_warning_only(family):
    family.person("Person P", birth="2045-07-17")
    family.person("Person A", birth="2000-07-28")
    family.parent("Person A", "Person P")
    findings = conflicts.find(family.build(), {"Person A"})
    assert findings.conflicts == []
    assert [w.as_dict() for w in findings.warnings] == [
        {"kind": "parent_born_after_child", "entries": ["Person P", "Person A"]},
    ]
