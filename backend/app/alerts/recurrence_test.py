"""Tests for repeating dates; the rules mirror frontend recurrenceUtils.ts."""
from datetime import date
from itertools import islice

from app.alerts import recurrence


def _first(value, start, n=4, from_date=None):
    return list(islice(recurrence.occurrences(value, start, from_date), n))


def test_a_date_without_repeat_occurs_once():
    assert _first({}, date(2045, 7, 17)) == [date(2045, 7, 17)]


def test_daily_weekly_with_interval():
    assert _first({"repeat": "daily", "repeatInterval": 2}, date(2045, 7, 17), 3) == [
        date(2045, 7, 17), date(2045, 7, 19), date(2045, 7, 21),
    ]
    assert _first({"repeat": "weekly"}, date(2045, 7, 17), 2) == [
        date(2045, 7, 17), date(2045, 7, 24),
    ]


def test_month_steps_overflow_like_javascript():
    assert _first({"repeat": "monthly"}, date(2045, 1, 31), 3) == [
        date(2045, 1, 31), date(2045, 3, 3), date(2045, 3, 31),
    ]


def test_february_29_plus_a_year_is_march_1():
    assert _first({"repeat": "yearly"}, date(2044, 2, 29), 2) == [date(2044, 2, 29), date(2045, 3, 1)]


def test_until_and_exceptions_are_honoured():
    value = {"repeat": "daily", "repeatUntil": "2045-07-20", "repeatExceptions": ["2045-07-18"]}
    assert _first(value, date(2045, 7, 17), 10) == [
        date(2045, 7, 17), date(2045, 7, 19), date(2045, 7, 20),
    ]


def test_iteration_can_start_near_a_later_day():
    value = {"repeat": "daily"}
    found = _first(value, date(2000, 7, 28), 1, from_date=date(2045, 7, 17))
    assert found[0] <= date(2045, 7, 17)
    assert (date(2045, 7, 17) - found[0]).days <= 2


def test_parse_date_reads_the_date_part():
    assert recurrence.parse_date("2045-07-17T09:00") == date(2045, 7, 17)
    assert recurrence.parse_date("nonsense") is None
