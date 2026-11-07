"""Tests for the generations of a family tree."""
from app.family_tree.generations import VIRTUAL_PREFIX, place, rank


def test_rank_orders_parent_nuances_with_unspecified_last():
    assert rank("kin.parent.biological") < rank("kin.parent.adoptive") < rank("kin.parent.step") < rank(None)


def test_root_is_generation_zero_and_children_sit_below_their_anchor(family):
    family.people_named("Person A", "Person B", "Person C")
    family.parent("Person B", "Person A")
    family.parent("Person C", "Person B")
    placement = place(family.build(), "Person B", {"Person A", "Person B", "Person C"})
    assert placement.generation == {"Person A": -1, "Person B": 0, "Person C": 1}


def test_the_mother_anchors_a_child_whatever_the_other_parent_says(family):
    # The father is one generation further down than the mother (his line is
    # longer); the child follows the mother.
    family.person("Person A")
    family.person("Person M", sex="female")
    family.person("Person F", sex="male")
    family.person("Person G")
    family.person("Person C")
    family.parent("Person M", "Person A")
    family.parent("Person F", "Person G")
    family.parent("Person G", "Person A")
    family.parent("Person C", "Person F")
    family.parent("Person C", "Person M")
    members = {"Person A", "Person M", "Person F", "Person G", "Person C"}
    placement = place(family.build(), "Person A", members)
    assert placement.anchor["Person C"] == "Person M"
    assert placement.generation["Person C"] == placement.generation["Person M"] + 1 == 2
    assert placement.generation["Person F"] == 2


def test_without_a_mother_the_strongest_link_anchors(family):
    family.people_named("Person A", "Person B", "Person C")
    family.parent("Person C", "Person A", nuance="kin.parent.step")
    family.parent("Person C", "Person B", nuance="kin.parent.adoptive")
    placement = place(family.build(), "Person C", {"Person A", "Person B", "Person C"})
    assert placement.anchor["Person C"] == "Person B"


def test_two_mothers_anchor_at_the_first_and_warn(family):
    family.person("Person M", sex="female")
    family.person("Person N", sex="female")
    family.person("Person C")
    family.parent("Person C", "Person M")
    family.parent("Person C", "Person N")
    placement = place(family.build(), "Person C", {"Person M", "Person N", "Person C"})
    assert placement.anchor["Person C"] == "Person M"
    assert placement.warnings["Person C"] == ["two_mothers"]


def test_partners_share_a_row(family):
    family.people_named("Person A", "Person B")
    family.partner("Person A", "Person B")
    placement = place(family.build(), "Person A", {"Person A", "Person B"})
    assert placement.generation["Person B"] == 0


def test_a_cycle_is_broken_at_one_link_and_still_placed(family):
    family.people_named("Person A", "Person B", "Person C")
    family.parent("Person B", "Person A")
    family.parent("Person C", "Person B")
    family.parent("Person A", "Person C")
    placement = place(family.build(), "Person A", {"Person A", "Person B", "Person C"})
    assert len(placement.broken) == 1
    assert set(placement.generation) == {"Person A", "Person B", "Person C"}
    parent, child = next(iter(placement.broken))
    assert placement.anchor.get(child) != parent


def test_siblings_without_a_shared_parent_get_one_virtual_parent(family):
    family.people_named("Person A", "Person B", "Person C")
    family.sibling("Person A", "Person B")
    family.sibling("Person B", "Person C")
    placement = place(family.build(), "Person A", {"Person A", "Person B", "Person C"})
    assert len(placement.virtual) == 1
    virtual = placement.virtual[0]
    assert virtual.id.startswith(VIRTUAL_PREFIX)
    assert set(virtual.children) == {"Person A", "Person B", "Person C"}
    assert virtual.generation == -1


def test_siblings_with_a_shared_parent_need_no_virtual_parent(family):
    family.people_named("Person P", "Person A", "Person B")
    family.parent("Person A", "Person P")
    family.parent("Person B", "Person P")
    family.sibling("Person A", "Person B")
    placement = place(family.build(), "Person A", {"Person P", "Person A", "Person B"})
    assert placement.virtual == []
