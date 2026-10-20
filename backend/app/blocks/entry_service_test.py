"""
Tests for the pure helpers of the entry service.

The database steps (``write_value``, ``copy_entry``, ``apply_template``) are
covered end to end through the endpoints in ``database_router_test.py``.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.blocks import entry_service


def _schema(type_="relation", **config):
    return SimpleNamespace(id=uuid.uuid4(), name="Property A", type=type_, config=config or None)


# ─── Relation kinds ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("schema, expected", [
    (_schema(direction="bilateral"), True),
    (_schema(direction="bilateral_self"), True),
    (_schema(direction="unilateral"), False),
    (_schema(), False),
    (_schema("parent_item", direction="bilateral"), False),
])
def test_is_bilateral(schema, expected):
    assert entry_service.is_bilateral(schema) is expected


def test_is_relation_timeline_needs_a_relation_with_the_flag():
    assert entry_service.is_relation_timeline(_schema(hasTimeline=True))
    assert not entry_service.is_relation_timeline(_schema())
    assert not entry_service.is_relation_timeline(_schema("text", hasTimeline=True))


def test_foreign_target_ignores_the_own_database():
    own, other = uuid.uuid4(), uuid.uuid4()
    assert entry_service.foreign_target(_schema(target_database_id=str(other)), own) == str(other)
    assert entry_service.foreign_target(_schema(target_database_id=str(own)), own) is None
    assert entry_service.foreign_target(_schema(), own) is None


def test_resolve_partner_schema_finds_the_partner_by_id():
    partner = _schema("sub_item")
    schema = _schema("parent_item", partner_schema_id=str(partner.id))
    assert entry_service.resolve_partner_schema([schema, partner], schema) is partner
    assert entry_service.resolve_partner_schema([schema], schema) is None
    assert entry_service.resolve_partner_schema([partner], _schema("parent_item")) is None


# ─── Checks ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("type_", ["id", "created_by", "created_time", "last_edited_by",
                                   "last_edited_time", "formula", "rollup"])
def test_system_managed_properties_are_not_writable(type_):
    with pytest.raises(HTTPException) as exc:
        entry_service.check_writable(_schema(type_))
    assert exc.value.status_code == 422


@pytest.mark.parametrize("type_", ["text", "relation", "parent_item", "sub_item"])
def test_other_properties_are_writable(type_):
    entry_service.check_writable(_schema(type_))


def test_parent_item_refuses_several_parents_and_invalid_ids():
    schema = _schema("parent_item")
    with pytest.raises(HTTPException) as several:
        entry_service.check_parent_item_value(None, uuid.uuid4(), schema, {
            "related_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
        })
    assert several.value.status_code == 422
    with pytest.raises(HTTPException) as invalid:
        entry_service.check_parent_item_value(None, uuid.uuid4(), schema, {
            "related_ids": ["not-an-id"],
        })
    assert invalid.value.status_code == 422


def test_parent_item_check_ignores_other_types_and_cleared_values():
    entry_service.check_parent_item_value(None, uuid.uuid4(), _schema("text"), {"related_ids": ["a", "b"]})
    entry_service.check_parent_item_value(None, uuid.uuid4(), _schema("parent_item"), None)
    entry_service.check_parent_item_value(None, uuid.uuid4(), _schema("parent_item"), {"related_ids": []})


def test_timeline_relation_refuses_a_direct_timeline_write():
    with pytest.raises(HTTPException) as exc:
        entry_service.check_timeline_value(_schema(hasTimeline=True), {"_timeline": {}})
    assert exc.value.status_code == 422
    entry_service.check_timeline_value(_schema(hasTimeline=True), {"relationPool": {}})


def test_scalar_timeline_is_checked_for_structure():
    schema = _schema("text", hasTimeline=True)
    with pytest.raises(HTTPException) as exc:
        entry_service.check_timeline_value(schema, {"_timeline": "not-a-dict"})
    assert exc.value.status_code == 422
    entry_service.check_timeline_value(schema, {"_timeline": {"": {"text": "Value A"}}})
    entry_service.check_timeline_value(_schema("text"), {"_timeline": "not-a-dict"})


# ─── Stored values ───────────────────────────────────────────────────────────


def test_stored_timeline_relation_derives_its_slots_and_drops_stray_labels():
    pool = {"a": ["2000-07-28T08:00:00→"]}
    stored = entry_service.build_stored_value(_schema(hasTimeline=True), {
        "relationPool": pool,
        "nuancePool": {"a": {"2000-07-28T08:00:00→": "label-a"}, "ghost": {"": "label-b"}},
        "_timeline": {"ignored": {}},
    })
    assert stored == {
        "relationPool": pool,
        "nuancePool": {"a": {"2000-07-28T08:00:00→": "label-a"}},
        "_timeline": {
            "2000-07-28T08:00:00→": {"related_ids": ["a"], "nuances": {"a": "label-a"}},
        },
    }


def test_stored_flat_relation_keeps_links_and_labels_of_linked_entries():
    stored = entry_service.build_stored_value(_schema(), {
        "related_ids": ["a"], "nuances": {"a": "label-a", "ghost": "label-b"}, "extra": 1,
    })
    assert stored == {"related_ids": ["a"], "nuances": {"a": "label-a"}}


def test_other_values_are_stored_as_sent():
    value = {"text": "Value A"}
    assert entry_service.build_stored_value(_schema("text"), value) is value
    assert entry_service.build_stored_value(_schema(), None) is None
