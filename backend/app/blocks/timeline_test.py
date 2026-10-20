"""
Tests for the timeline helpers of database properties.

Pure functions, no database: period key parsing, the derivation of relation
timelines from their pools, nuance sanitising and the structure check of
scalar timelines. The endpoints that use them are covered in
``database_router_test.py``.
"""
import pytest

from app.blocks.timeline import (
    parse_pool_range,
    pool_to_timeline,
    sanitize_flat_nuances,
    sanitize_nuance_pool,
    validate_timeline_value,
)


# ── Period keys ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key, sides", [
    ("2000-07-28T08:00:00→2045-07-17T18:05:00", ("2000-07-28T08:00:00", "2045-07-17T18:05:00")),
    ("2000-07-28T08:00:00→", ("2000-07-28T08:00:00", None)),
    ("→2045-07-17T18:05:00", (None, "2045-07-17T18:05:00")),
    ("", (None, None)),
    ("2000-07-28T08:00:00", ("2000-07-28T08:00:00", None)),
])
def test_parse_pool_range_splits_the_sides(key, sides):
    """An open side is None; a key without arrow is a start."""
    assert parse_pool_range(key) == sides


# ── Timeline: validate_timeline_value ───────────────────────────────────────

def test_validate_timeline_empty_is_valid():
    assert validate_timeline_value({"_timeline": {}}) is None


def test_validate_timeline_singleton_empty_key():
    assert validate_timeline_value({"_timeline": {"": {"text": "x"}}}) is None


def test_validate_timeline_empty_key_with_others_invalid():
    val = {"_timeline": {"": {"text": "x"}, "2024-01-01T00:00:00→": {"text": "y"}}}
    assert validate_timeline_value(val) is not None


def test_validate_timeline_valid_full_range():
    val = {"_timeline": {
        "2023-01-01T00:00:00→2023-12-31T23:59:59": {"text": "a"},
        "2024-01-01T00:00:00→": {"text": "b"},
    }}
    assert validate_timeline_value(val) is None


def test_validate_timeline_valid_until_range_first():
    val = {"_timeline": {
        "→2022-12-31T23:59:59": {"text": "before"},
        "2023-01-01T00:00:00→": {"text": "after"},
    }}
    assert validate_timeline_value(val) is None


def test_validate_timeline_until_not_first_is_invalid():
    # The function sorts keys chronologically before validating, so →until
    # always ends up at position 0 (valid). No overlap exists between
    # →2022-06-30 and 2023-01-01→ so the function returns None (valid).
    val = {"_timeline": {
        "2023-01-01T00:00:00→2023-12-31T23:59:59": {"text": "a"},
        "→2022-06-30T23:59:59": {"text": "before"},
    }}
    assert validate_timeline_value(val) is None


def test_validate_timeline_since_not_last_is_invalid():
    val = {"_timeline": {
        "2023-01-01T00:00:00→": {"text": "open"},
        "2024-01-01T00:00:00→2024-12-31T23:59:59": {"text": "closed"},
    }}
    assert validate_timeline_value(val) is not None


def test_validate_timeline_overlap_is_invalid():
    val = {"_timeline": {
        "2023-01-01T00:00:00→2024-06-30T23:59:59": {"text": "a"},
        "2024-01-01T00:00:00→": {"text": "b"},
    }}
    assert validate_timeline_value(val) is not None


def test_validate_timeline_not_a_dict_is_invalid():
    assert validate_timeline_value({"_timeline": "oops"}) is not None


# ── Timeline: pool_to_timeline sweepline ────────────────────────────────────

def test_pool_to_timeline_empty_pool():
    assert pool_to_timeline({}) == {}


def test_pool_to_timeline_single_since():
    pool = {"uuid-c": ["2023-01-01T00:00:00→"]}
    timeline = pool_to_timeline(pool)
    assert "2023-01-01T00:00:00→" in timeline
    assert timeline["2023-01-01T00:00:00→"]["related_ids"] == ["uuid-c"]


def test_pool_to_timeline_full_example():
    pool = {
        "uuid-a": ["2024-01-01T00:00:00→2024-12-31T23:59:59", "2026-01-01T00:00:00→"],
        "uuid-c": ["2023-01-01T00:00:00→"],
    }
    timeline = pool_to_timeline(pool)
    # uuid-c alone in 2023
    assert any(
        "uuid-c" in v.get("related_ids", []) and "uuid-a" not in v.get("related_ids", [])
        for k, v in timeline.items()
        if k.startswith("2023")
    )
    # Both active in 2024
    assert any(
        "uuid-a" in v.get("related_ids", []) and "uuid-c" in v.get("related_ids", [])
        for k, v in timeline.items()
        if k.startswith("2024")
    )
    # Open-ended slot has both
    open_slots = [k for k in timeline if k.endswith("→")]
    assert any(
        "uuid-a" in timeline[k].get("related_ids", [])
        for k in open_slots
        if k.startswith("2026")
    )


def test_pool_to_timeline_ancient_dates_keep_correct_boundaries():
    """Regression: years < 1000 must produce zero-padded change-points.

    strftime("%Y") drops the leading zeros of ancient years on glibc, so a
    derived change-point for year 30 rendered as "30-..." and string-sorted
    after a modern "2020-..." boundary. That mis-ordering swapped slot bounds
    (the bounded slot inherited the next entry's start as its end) and dropped
    the "until" slot entirely. Each slot must keep its own original bounds.
    """
    pool = {
        "a-id": ["→0010-01-01T00:00:00"],
        "b-id": ["0010-02-01T00:00:00→0030-01-01T00:00:00"],
        "c-id": ["2045-07-17T09:00:00→"],
    }
    timeline = pool_to_timeline(pool)
    assert timeline == {
        "→0010-01-01T00:00:00": {"related_ids": ["a-id"]},
        "0010-02-01T00:00:00→0030-01-01T00:00:00": {"related_ids": ["b-id"]},
        "2045-07-17T09:00:00→": {"related_ids": ["c-id"]},
    }


def test_pool_to_timeline_ancient_dates_change_points_zero_padded():
    """Every derived slot key must use a 4-digit, zero-padded year so the keys
    sort chronologically by plain string comparison."""
    pool = {
        "a": ["0020-01-01T00:00:00→0021-01-01T00:00:00"],
        "b": ["0021-06-01T00:00:00→"],
    }
    timeline = pool_to_timeline(pool)
    for key in timeline:
        for boundary in key.split("→"):
            if boundary:
                # Year segment is the part before the first '-', always 4 chars.
                assert len(boundary.split("-", 1)[0]) == 4, key


# ── pool_to_timeline: always-valid and invalid-timestamp edge cases ──────────

def test_pool_to_timeline_always_valid_only():
    """A pool with only "" ranges emits a single "" slot."""
    pool = {"uuid-a": [""], "uuid-b": [""]}
    timeline = pool_to_timeline(pool)
    assert list(timeline.keys()) == [""]
    assert set(timeline[""]["related_ids"]) == {"uuid-a", "uuid-b"}


def test_pool_to_timeline_always_valid_mixed_with_bounded():
    """Always-valid UIDs appear in every bounded slot."""
    pool = {
        "always": [""],
        "bounded": ["2024-01-01T00:00:00→2024-12-31T23:59:59"],
    }
    timeline = pool_to_timeline(pool)
    # No "" key when bounded slots exist
    assert "" not in timeline
    # Every slot contains the always-valid uid
    for slot_val in timeline.values():
        assert "always" in slot_val["related_ids"]


def test_pool_to_timeline_always_valid_before_first_since():
    """
    Regression: always-valid UIDs must get a leading slot before the earliest
    bounded start. Without it, a pool of always-valid links plus one "since"
    link collapsed into a single slot starting at that "since" date, and the
    period in which only the always-valid links existed disappeared.
    """
    pool = {
        "a": [""],
        "b": [""],
        "c": ["2100-04-01T10:00:00→"],
    }
    timeline = pool_to_timeline(pool)
    assert timeline == {
        "→2100-04-01T09:59:00": {"related_ids": ["a", "b"]},
        "2100-04-01T10:00:00→": {"related_ids": ["a", "b", "c"]},
    }
    assert validate_timeline_value({"_timeline": timeline}) is None


def test_pool_to_timeline_always_valid_around_bounded_range():
    """Always-valid UIDs fill the periods before, during and after a bounded range."""
    pool = {
        "a": [""],
        "c": ["2100-04-01T00:00:00→2100-12-31T23:59:00"],
    }
    timeline = pool_to_timeline(pool)
    assert timeline == {
        "→2100-03-31T23:59:00": {"related_ids": ["a"]},
        "2100-04-01T00:00:00→2100-12-31T23:59:00": {"related_ids": ["a", "c"]},
        "2101-01-01T00:00:00→": {"related_ids": ["a"]},
    }
    assert validate_timeline_value({"_timeline": timeline}) is None


def test_pool_to_timeline_always_valid_with_until_range_shares_leading_slot():
    """An "until" range and an always-valid UID share the leading slot."""
    pool = {
        "a": [""],
        "c": ["→2100-03-31T23:59:00"],
    }
    timeline = pool_to_timeline(pool)
    assert timeline == {
        "→2100-03-31T23:59:00": {"related_ids": ["a", "c"]},
        "2100-04-01T00:00:00→": {"related_ids": ["a"]},
    }
    assert validate_timeline_value({"_timeline": timeline}) is None


def test_pool_to_timeline_always_valid_nuance_in_leading_slot():
    """The always-valid nuance label is carried into the leading slot as well."""
    pool = {"a": [""], "c": ["2100-04-01T10:00:00→"]}
    nuance_pool = {
        "a": {"": "first"},
        "c": {"2100-04-01T10:00:00→": "second"},
    }
    timeline = pool_to_timeline(pool, nuance_pool)
    assert timeline["→2100-04-01T09:59:00"]["nuances"] == {"a": "first"}
    assert timeline["2100-04-01T10:00:00→"]["nuances"] == {"a": "first", "c": "second"}


def test_pool_to_timeline_invalid_timestamp_skipped():
    """An invalid timestamp string (e.g. '2024') is skipped without crashing."""
    pool = {"uuid-a": ["2024"]}
    # Should not raise; unparseable timestamps are silently dropped
    result = pool_to_timeline(pool)
    assert isinstance(result, dict)


# ── pool_to_timeline: minute resolution ─────────────────────────────────────

def test_pool_to_timeline_end_and_next_start_in_consecutive_minutes_leave_no_gap():
    """
    Regression: an end covers its whole minute. The editor stores minutes, so
    a range ending at 14:14 and the next starting at 14:15 are seamless. The
    sweepline used to treat the end as an instant and place the next
    change-point one second later, which produced a 59-second slot holding
    only the links that neither ended nor started in that minute, and started
    the slot after a final end one second (instead of one minute) later.
    """
    pool = {
        "a": ["2000-07-28T08:00:00→2010-03-01T14:14:00", "2010-03-01T14:15:00→"],
        "b": [
            "2000-07-28T08:00:00→2020-05-05T16:40:00",
            "2020-05-05T16:41:00→2045-07-17T18:05:00",
        ],
    }
    nuance_pool = {
        "a": {
            "2000-07-28T08:00:00→2010-03-01T14:14:00": "first",
            "2010-03-01T14:15:00→": "second",
        },
        "b": {
            "2000-07-28T08:00:00→2020-05-05T16:40:00": "first",
            "2020-05-05T16:41:00→2045-07-17T18:05:00": "second",
        },
    }
    timeline = pool_to_timeline(pool, nuance_pool)
    assert timeline == {
        "2000-07-28T08:00:00→2010-03-01T14:14:00": {
            "related_ids": ["a", "b"], "nuances": {"a": "first", "b": "first"},
        },
        "2010-03-01T14:15:00→2020-05-05T16:40:00": {
            "related_ids": ["a", "b"], "nuances": {"a": "second", "b": "first"},
        },
        "2020-05-05T16:41:00→2045-07-17T18:05:00": {
            "related_ids": ["a", "b"], "nuances": {"a": "second", "b": "second"},
        },
        "2045-07-17T18:06:00→": {
            "related_ids": ["a"], "nuances": {"a": "second"},
        },
    }
    assert validate_timeline_value({"_timeline": timeline}) is None


def test_pool_to_timeline_slot_end_is_the_last_minute_it_covers():
    """A slot ends one minute before the next change-point, in canonical form."""
    pool = {"a": ["2000-01-01T00:00:00→"], "b": ["2000-06-01T12:00:00→"]}
    timeline = pool_to_timeline(pool)
    assert list(timeline) == [
        "2000-01-01T00:00:00→2000-06-01T11:59:00",
        "2000-06-01T12:00:00→",
    ]


def test_pool_to_timeline_one_instant_in_two_forms_is_one_change_point():
    """
    Boundaries are compared as instants, not as text: a date without a time is
    midnight, seconds are ignored, and both collapse onto the canonical key.
    """
    pool = {
        "a": ["2000-01-01T00:00:00→2000-12-31T23:59:30"],
        "b": ["2001-01-01→"],
    }
    timeline = pool_to_timeline(pool)
    assert timeline == {
        "2000-01-01T00:00:00→2000-12-31T23:59:00": {"related_ids": ["a"]},
        "2001-01-01T00:00:00→": {"related_ids": ["b"]},
    }


def test_pool_to_timeline_range_ending_before_its_start_is_ignored():
    """An inverted range covers no time and adds no change-points."""
    pool = {
        "a": ["2001-01-01T00:00:00→2000-01-01T00:00:00"],
        "b": ["1999-01-01T00:00:00→"],
    }
    assert pool_to_timeline(pool) == {
        "1999-01-01T00:00:00→": {"related_ids": ["b"]},
    }


def test_pool_to_timeline_end_at_the_last_representable_minute_stays_open():
    """An end that cannot be followed by another minute never ends."""
    pool = {"a": ["2000-01-01T00:00:00→9999-12-31T23:59:00"]}
    assert pool_to_timeline(pool) == {
        "2000-01-01T00:00:00→": {"related_ids": ["a"]},
    }


def test_pool_to_timeline_no_leading_slot_before_the_earliest_minute():
    """Nothing lies before the earliest representable minute."""
    pool = {"a": [""], "b": ["0001-01-01T00:00:00→"]}
    assert pool_to_timeline(pool) == {
        "0001-01-01T00:00:00→": {"related_ids": ["a", "b"]},
    }


# ── Nuance: pool_to_timeline threading + sanitizers (pure) ───────────────────

def test_pool_to_timeline_nuance_carried_into_slot():
    """A per-(uid, range) label surfaces in the matching slot's nuances map."""
    pool = {"a": ["2024-01-01T00:00:00→"]}
    nuance_pool = {"a": {"2024-01-01T00:00:00→": "interim"}}
    timeline = pool_to_timeline(pool, nuance_pool)
    slot = timeline["2024-01-01T00:00:00→"]
    assert slot["related_ids"] == ["a"]
    assert slot["nuances"] == {"a": "interim"}


def test_pool_to_timeline_nuance_varies_per_range():
    """The same uid carries different labels in different ranges."""
    pool = {"a": [
        "2024-01-01T00:00:00→2024-12-31T23:59:00",
        "2025-01-01T00:00:00→",
    ]}
    nuance_pool = {"a": {
        "2024-01-01T00:00:00→2024-12-31T23:59:00": "interim",
        "2025-01-01T00:00:00→": "confirmed",
    }}
    timeline = pool_to_timeline(pool, nuance_pool)
    assert timeline["2024-01-01T00:00:00→2024-12-31T23:59:00"]["nuances"] == {"a": "interim"}
    assert timeline["2025-01-01T00:00:00→"]["nuances"] == {"a": "confirmed"}


def test_pool_to_timeline_always_valid_nuance():
    """An always-valid uid carries its '' nuance into the sole slot."""
    timeline = pool_to_timeline({"a": [""]}, {"a": {"": "label-a"}})
    assert timeline[""]["nuances"] == {"a": "label-a"}


def test_pool_to_timeline_partial_nuance_in_shared_slot():
    """Only labelled uids appear in nuances; unlabelled ones are omitted."""
    timeline = pool_to_timeline({"a": [""], "b": [""]}, {"a": {"": "lead"}})
    assert set(timeline[""]["related_ids"]) == {"a", "b"}
    assert timeline[""]["nuances"] == {"a": "lead"}


def test_pool_to_timeline_empty_nuance_pool_adds_no_key():
    """Regression: no nuance_pool (or empty) must not add a 'nuances' key."""
    pool = {
        "a": ["2024-01-01T00:00:00→2024-12-31T23:59:59"],
        "b": ["2025-01-01T00:00:00→"],
    }
    assert all("nuances" not in slot for slot in pool_to_timeline(pool).values())
    assert all("nuances" not in slot for slot in pool_to_timeline(pool, {}).values())


def test_sanitize_nuance_pool_drops_unknown_pairs_and_blanks():
    pool = {"a": ["x"]}
    raw = {"a": {"x": "keep", "y": "drop-range"}, "ghost": {"x": "drop-uid"}}
    assert sanitize_nuance_pool(raw, pool) == {"a": {"x": "keep"}}
    assert sanitize_nuance_pool({"a": {"x": "   "}}, pool) == {}
    assert sanitize_nuance_pool("not-a-dict", pool) == {}


def test_sanitize_flat_nuances_keeps_only_linked_uids():
    assert sanitize_flat_nuances({"a": "k", "ghost": "q"}, ["a"]) == {"a": "k"}
    assert sanitize_flat_nuances({"a": "  "}, ["a"]) == {}
    assert sanitize_flat_nuances(None, ["a"]) == {}
