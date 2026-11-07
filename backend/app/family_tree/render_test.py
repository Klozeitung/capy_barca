"""Tests for what the family tree view draws: periods and the property line."""
import uuid
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.family_tree import render

LABELS = {"kin.parent.step": "step", "kin.partner.marriage": "marriage"}


def test_a_biological_link_begins_at_birth_and_survives_death(family):
    family.person("Person P", death="2030-01-01")
    family.person("Person C", birth="2000-07-28")
    family.parent("Person C", "Person P")
    [segment] = render.parent_segments(family.build(), "Person P", "Person C", LABELS)
    assert segment["start"] == "2000-07-28T00:00"
    assert segment["end"] is None


def test_step_parenthood_and_partnerships_end_at_the_earlier_death(family):
    family.person("Person P", death="2030-01-01")
    family.person("Person C", death="2045-07-17")
    family.parent("Person C", "Person P", nuance="kin.parent.step", range_key="2010-01-01T00:00:00→")
    family.partner("Person P", "Person C")
    graph = family.build()
    [step] = render.parent_segments(graph, "Person P", "Person C", LABELS)
    assert (step["start"], step["end"], step["label"]) == ("2010-01-01T00:00", "2030-01-01T00:00", "step")
    [partners] = render.partner_segments(graph, "Person P", "Person C", LABELS)
    assert partners["end"] == "2030-01-01T00:00"
    assert partners["label"] == "marriage"


def _schema(type_, **config):
    return SimpleNamespace(id=uuid.uuid4(), name="Property A", type=type_, config=config)


ZONE = ZoneInfo("UTC")


def test_property_tokens_per_type():
    select = _schema("select", options=[{"id": "o-1", "label": "Value A"}, {"id": "o-2", "label": "Value B"}])
    assert render.property_entry(select, {"options": ["o-1", "o-2"]}, {}, ZONE)["tokens"] == [
        {"text": "Value A"}, {"text": "Value B"},
    ]
    assert render.property_entry(_schema("number"), {"number": 3.0}, {}, ZONE)["tokens"] == [{"text": "3"}]
    assert render.property_entry(_schema("checkbox"), {"checked": True}, {}, ZONE)["tokens"] == [{"check": True}]
    assert render.property_entry(_schema("date"), {"start": "2000-07-28"}, {}, ZONE)["tokens"] == [
        {"date": "2000-07-28", "end": None},
    ]
    relation = _schema("relation")
    assert render.property_entry(relation, {"related_ids": ["x", "y"]}, {"x": "City X"}, ZONE)["tokens"] == [
        {"text": "City X"},
    ]
    assert render.property_entry(_schema("text"), None, {}, ZONE)["tokens"] == []


def test_computed_results_show_their_stored_value():
    rollup = _schema("rollup")
    entry = render.property_entry(rollup, {"result": ["Value A", 2, {"start": "2045-07-17"}]}, {}, ZONE)
    assert entry["tokens"] == [{"text": "Value A"}, {"text": "2"}, {"date": "2045-07-17", "end": None}]


def test_a_timelined_value_comes_as_slots_in_order():
    value = {"_timeline": {
        "2010-01-01T00:00:00→": {"text": "Value B"},
        "→2009-12-31T23:59:00": {"text": "Value A"},
    }}
    entry = render.property_entry(_schema("text"), value, {}, ZONE)
    assert entry["slots"] == [
        {"start": None, "end": "2009-12-31T23:59", "tokens": [{"text": "Value A"}]},
        {"start": "2010-01-01T00:00", "end": None, "tokens": [{"text": "Value B"}]},
    ]


def test_an_overlay_needs_a_two_way_relation_of_its_own_database_outside_the_set():
    database = uuid.uuid4()

    def relation(**config):
        return SimpleNamespace(database_id=database, type="relation", config={"target_database_id": str(database), **config})

    assert render.overlay_schema_valid(relation(direction="bilateral_self"), database)
    assert render.overlay_schema_valid(relation(direction="bilateral"), database)
    assert not render.overlay_schema_valid(relation(direction="unilateral"), database)
    assert not render.overlay_schema_valid(relation(direction="bilateral_self", kinRole="partners"), database)
    assert not render.overlay_schema_valid(None, database)
