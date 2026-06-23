"""
Tests for the formula engine.

Pure unit tests – no database, no fixtures.
"""

import math
from datetime import datetime as _DT, timezone

import pytest

from app.blocks.formula_engine import (
    FormulaError,
    FormulaResult,
    evaluate,
    extract_prop_names,
    rename_prop_in_expression,
    validate_syntax,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def ev(expr: str, ctx: dict | None = None) -> object:
    """Evaluate expression and return result scalar; raise on error."""
    result = evaluate(expr, ctx or {})
    if result.error:
        raise AssertionError(f"Unexpected formula error: {result.error}")
    return result.result


def err(expr: str, ctx: dict | None = None) -> str:
    """Evaluate expression and return the error message (fail if no error)."""
    result = evaluate(expr, ctx or {})
    assert result.error is not None, f"Expected error but got result: {result.result}"
    return result.error


# ─── Literals ────────────────────────────────────────────────────────────────


def test_integer_literal():
    assert ev("42") == 42


def test_float_literal():
    assert ev("3.14") == pytest.approx(3.14)


def test_string_literal_double_quotes():
    assert ev('"hello"') == "hello"


def test_string_literal_single_quotes():
    assert ev("'hello'") == "hello"


def test_string_escape_newline():
    assert ev(r"'a\nb'") == "a\nb"


def test_string_escape_tab():
    assert ev(r"'a\tb'") == "a\tb"


def test_true_literal():
    assert ev("true") is True


def test_false_literal():
    assert ev("false") is False


def test_null_literal():
    assert ev("null") is None


# ─── Arithmetic ───────────────────────────────────────────────────────────────


def test_addition():
    assert ev("2 + 3") == 5


def test_subtraction():
    assert ev("10 - 4") == 6


def test_multiplication():
    assert ev("3 * 7") == 21


def test_division():
    assert ev("15 / 4") == pytest.approx(3.75)


def test_modulo():
    assert ev("10 % 3") == 1


def test_power():
    assert ev("2 ^ 10") == 1024


def test_power_right_associative():
    # 2^(3^2) = 2^9 = 512
    assert ev("2 ^ 3 ^ 2") == 512


def test_unary_minus():
    assert ev("-5") == -5


def test_nested_unary_minus():
    assert ev("--3") == 3


def test_multiplication_over_addition_precedence():
    assert ev("2 + 3 * 4") == 14


def test_parentheses_override_precedence():
    assert ev("(2 + 3) * 4") == 20


def test_power_over_multiplication_precedence():
    assert ev("2 * 3 ^ 2") == 18


def test_division_by_zero_returns_error():
    assert "Division by zero" in err("1 / 0")


def test_modulo_by_zero_returns_error():
    assert "Modulo by zero" in err("5 % 0")


# ─── String operations ───────────────────────────────────────────────────────


def test_string_concatenation_with_plus():
    assert ev('"hello" + " world"') == "hello world"


def test_string_plus_number_coerces():
    assert ev('"x" + 1') == "x1"


def test_number_plus_string_coerces():
    assert ev('1 + "x"') == "1x"


# ─── Comparison ───────────────────────────────────────────────────────────────


def test_eq_true():
    assert ev("3 == 3") is True


def test_eq_false():
    assert ev("3 == 4") is False


def test_neq_true():
    assert ev("3 != 4") is True


def test_lt():
    assert ev("2 < 5") is True


def test_lte_equal():
    assert ev("5 <= 5") is True


def test_gt():
    assert ev("7 > 3") is True


def test_gte():
    assert ev("4 >= 5") is False


def test_eq_null_null():
    assert ev("null == null") is True


def test_eq_string():
    assert ev('"a" == "a"') is True


# ─── Logical operators ────────────────────────────────────────────────────────


def test_and_true():
    assert ev("true and true") is True


def test_and_false():
    assert ev("true and false") is False


def test_or_true():
    assert ev("false or true") is True


def test_or_false():
    assert ev("false or false") is False


def test_not_true():
    assert ev("not true") is False


def test_not_false():
    assert ev("not false") is True


def test_not_unary_operator():
    assert ev("not false") is True


def test_and_short_circuits():
    # Second branch would cause divide-by-zero but should never execute
    result = evaluate("false and (1 / 0)", {})
    assert result.result is False
    assert result.error is None


def test_or_short_circuits():
    result = evaluate("true or (1 / 0)", {})
    assert result.result is True
    assert result.error is None


def test_not_binds_tighter_than_and():
    # (not false) and true  = true
    assert ev("not false and true") is True


# ─── prop() ───────────────────────────────────────────────────────────────────


def test_prop_returns_value_from_context():
    assert ev("prop('Price')", {"Price": 9.99}) == pytest.approx(9.99)


def test_prop_missing_returns_none():
    assert ev("prop('Missing')", {}) is None


def test_prop_with_double_quotes():
    assert ev('prop("Name")', {"Name": "Alice"}) == "Alice"


def test_prop_arithmetic():
    assert ev("prop('A') * prop('B')", {"A": 3, "B": 4}) == 12


def test_prop_chained():
    assert ev("prop('X') + prop('Y') + prop('Z')", {"X": 1, "Y": 2, "Z": 3}) == 6


# ─── Built-in functions ───────────────────────────────────────────────────────


def test_if_true_branch():
    assert ev("if(true, 'yes', 'no')") == "yes"


def test_if_false_branch():
    assert ev("if(false, 'yes', 'no')") == "no"


def test_if_with_comparison():
    assert ev("if(prop('x') > 5, 'big', 'small')", {"x": 10}) == "big"


def test_abs_positive():
    assert ev("abs(5)") == 5


def test_abs_negative():
    assert ev("abs(-7)") == 7


def test_round_no_digits():
    assert ev("round(3.7)") == 4


def test_round_with_digits():
    assert ev("round(3.14159, 2)") == pytest.approx(3.14)


def test_ceil():
    assert ev("ceil(2.1)") == 3


def test_floor():
    assert ev("floor(2.9)") == 2


def test_len_string():
    assert ev("len('hello')") == 5


def test_len_empty_string():
    assert ev("len('')") == 0


def test_len_null_returns_zero():
    assert ev("len(null)") == 0


def test_concat_two():
    assert ev("concat('hello', ' world')") == "hello world"


def test_concat_multiple():
    assert ev("concat('a', 'b', 'c')") == "abc"


def test_concat_with_number():
    assert ev("concat('count: ', 42)") == "count: 42"


# ─── empty() ──────────────────────────────────────────────────────────────────


def test_empty_none_is_true():
    assert ev("empty(null)") is True


def test_empty_empty_string_is_true():
    assert ev("empty('')") is True


def test_empty_nonempty_string_is_false():
    assert ev("empty('hello')") is False


def test_empty_zero_is_false():
    # 0 is a valid number value, not "empty"
    assert ev("empty(0)") is False


def test_empty_number_is_false():
    assert ev("empty(42)") is False


def test_empty_prop_missing_returns_true():
    # prop() returns None when the property has no value set
    assert ev("empty(prop('Score'))", {}) is True


def test_empty_prop_with_value_returns_false():
    assert ev("empty(prop('Score'))", {"Score": 50}) is False


def test_empty_used_as_guard_in_if():
    # Mirrors the real-world formula pattern that triggered the bug report
    ctx: dict = {}
    result = ev("if(empty(prop('Score')), 'n/a', 'ok')", ctx)
    assert result == "n/a"


def test_empty_wrong_arg_count():
    assert err("empty()")
    assert err("empty(null, null)")


def test_empty_empty_list_is_true():
    # Relation props with no links return [] — must be treated as empty
    assert ev("empty(prop('R'))", {"R": []}) is True


def test_empty_nonempty_list_is_false():
    assert ev("empty(prop('R'))", {"R": ["some-uuid"]}) is False


def test_empty_empty_dict_is_true():
    # percent_per_option with no data returns {} — also empty
    assert ev("empty(prop('D'))", {"D": {}}) is True


def test_empty_nonempty_dict_is_false():
    assert ev("empty(prop('D'))", {"D": {"A": 50.0}}) is False


def test_empty_zero_still_false():
    # 0 is a valid numeric value, not empty
    assert ev("empty(prop('N'))", {"N": 0}) is False


# ─── format() ─────────────────────────────────────────────────────────────────


def test_format_none_returns_empty_string():
    assert ev("format(null)") == ""


def test_format_string_passthrough():
    assert ev("format('hello')") == "hello"


def test_format_integer():
    assert ev("format(42)") == "42"


def test_format_float_whole_returns_no_decimal():
    assert ev("format(3.0)") == "3"


def test_format_float_fractional():
    assert ev("format(3.14)") == "3.14"


def test_format_bool_true():
    assert ev("format(true)") == "true"


def test_format_bool_false():
    assert ev("format(false)") == "false"


def test_format_prop_select_value():
    # Single-select values arrive as plain strings in the context
    assert ev("format(prop('Typ'))", {"Typ": "Option A"}) == "Option A"


def test_format_prop_missing_returns_empty_string():
    assert ev("format(prop('Missing'))", {}) == ""


def test_format_used_in_comparison():
    ctx = {"Typ": "Option A"}
    assert ev("format(prop('Typ')) == \"Option A\"", ctx) is True


def test_format_wrong_arg_count():
    assert err("format()")
    assert err("format('a', 'b')")


# format() with list / dict (show_original rollup, percent_per_option rollup)

def test_format_single_element_list_unwraps():
    # show_original rollup with one linked entry → comparison must work
    assert ev("format(prop('R'))", {"R": ["II"]}) == "II"


def test_format_empty_list_returns_empty_string():
    assert ev("format(prop('R'))", {"R": []}) == ""


def test_format_multi_element_list_joins():
    assert ev("format(prop('R'))", {"R": ["A", "B"]}) == "A, B"


def test_format_list_skips_none_elements():
    assert ev("format(prop('R'))", {"R": [None, "II", None]}) == "II"


def test_format_list_all_none_returns_empty_string():
    assert ev("format(prop('R'))", {"R": [None]}) == ""


def test_format_list_used_in_comparison():
    # Single-element list from show_original rollup unwraps for comparison
    ctx = {"Rollup Category": ["B"]}
    assert ev('format(prop("Rollup Category")) == "B"', ctx) is True


def test_format_empty_dict_returns_empty_string():
    assert ev("format(prop('D'))", {"D": {}}) == ""


def test_format_dict_renders_key_value():
    result = ev("format(prop('D'))", {"D": {"A": 50.0}})
    assert "A" in result


# ─── toNumber() ───────────────────────────────────────────────────────────────


def test_tonumber_integer_string():
    assert ev("toNumber('42')") == 42


def test_tonumber_float_string():
    assert ev("toNumber('3.14')") == pytest.approx(3.14)


def test_tonumber_number_passthrough():
    assert ev("toNumber(7)") == 7


def test_tonumber_bool_true():
    assert ev("toNumber(true)") == 1


def test_tonumber_bool_false():
    assert ev("toNumber(false)") == 0


def test_tonumber_none_returns_none():
    assert ev("toNumber(null)") is None


def test_tonumber_empty_string_returns_none():
    assert ev("toNumber('')") is None


def test_tonumber_non_numeric_string_returns_none():
    assert ev("toNumber('abc')") is None


def test_tonumber_used_as_guard_for_empty_branch():
    # Mirrors the exact Status formula pattern: "" branch yields None, not ""
    ctx = {"Status": "Archiviert", "Summe erledigte Aufgaben": 3, "Summe Aufgaben": 5}
    result = ev(
        "toNumber(if(prop('Status') == 'Archiviert', '',"
        "  if(prop('Status') == 'Fertig', 1, 0.5)))",
        ctx,
    )
    assert result is None


def test_tonumber_fertig_branch():
    ctx = {"Status": "Fertig", "Summe erledigte Aufgaben": 5, "Summe Aufgaben": 5}
    result = ev(
        "toNumber(if(prop('Status') == 'Archiviert', '',"
        "  if(prop('Status') == 'Fertig', 1, 0.5)))",
        ctx,
    )
    assert result == 1


def test_tonumber_wrong_arg_count():
    assert err("toNumber()")
    assert err("toNumber(1, 2)")


# ─── sum() / min() / max() / avg() ───────────────────────────────────────────


def test_sum_two_numbers():
    assert ev("sum(1, 2)") == 3


def test_sum_multiple():
    assert ev("sum(1, 2, 3, 4)") == 10


def test_sum_skips_none():
    assert ev("sum(1, null, 3)") == 4


def test_sum_all_none_returns_none():
    assert ev("sum(null, null)") is None


def test_sum_single_arg():
    assert ev("sum(7)") == 7


def test_sum_with_if_branches():
    # Category base score + modifier: typical sum(if(...), if(...)) pattern
    ctx = {"Category": "B", "Modifier": "High"}
    result = ev(
        "sum("
        "  if(format(prop('Category')) == 'B', 11, 0),"
        "  if(format(prop('Modifier')) == 'High', 2, 0)"
        ")",
        ctx,
    )
    assert result == 13


def test_min_numbers():
    assert ev("min(3, 1, 2)") == 1


def test_max_numbers():
    assert ev("max(3, 1, 2)") == 3


def test_avg_numbers():
    assert ev("avg(10, 20, 30)") == pytest.approx(20.0)


def test_min_skips_none():
    assert ev("min(5, null, 3)") == 3


def test_max_all_none_returns_none():
    assert ev("max(null, null)") is None


def test_sum_no_args_raises():
    assert err("sum()")






def test_not_function():
    assert ev("not(true)") is False


def test_and_function():
    assert ev("and(true, true, false)") is False


def test_or_function():
    assert ev("or(false, false, true)") is True


# ─── ifs() ───────────────────────────────────────────────────────────────────


def test_ifs_first_condition_true():
    assert ev("ifs(true, 'A', true, 'B', 'C')") == "A"


def test_ifs_second_condition_true():
    assert ev("ifs(false, 'A', true, 'B', 'C')") == "B"


def test_ifs_default_returned():
    assert ev("ifs(false, 'A', false, 'B', 'C')") == "C"


def test_ifs_no_match_no_default_returns_none():
    assert ev("ifs(false, 'A', false, 'B')") is None


def test_ifs_single_pair_match():
    assert ev("ifs(true, 42)") == 42


def test_ifs_single_pair_no_match_no_default():
    assert ev("ifs(false, 42)") is None


def test_ifs_with_comparison():
    ctx = {"Score": 75}
    result = ev("ifs(prop('Score') >= 90, 'A', prop('Score') >= 60, 'B', 'C')", ctx)
    assert result == "B"


def test_ifs_grade_boundaries():
    for score, expected in [(95, "A"), (80, "B"), (65, "C"), (45, "F")]:
        ctx = {"Score": score}
        result = ev(
            "ifs(prop('Score') >= 90, 'A', prop('Score') >= 75, 'B',"
            "    prop('Score') >= 60, 'C', 'F')",
            ctx,
        )
        assert result == expected, f"Score {score}: expected {expected}, got {result}"


def test_ifs_too_few_args_raises():
    assert err("ifs(true)")


# ─── Date & time functions ────────────────────────────────────────────────────

_ISO_DATE = "2024-03-15T14:30:45+00:00"  # Friday, week 11


def test_now_returns_datetime():
    result = ev("now()")
    assert isinstance(result, _DT)
    assert result.tzinfo is not None


def test_today_returns_midnight():
    result = ev("today()")
    assert isinstance(result, _DT)
    assert result.hour == 0
    assert result.minute == 0
    assert result.second == 0


def test_parsedate_iso_full():
    result = ev("parseDate('2024-03-15T14:30:00+00:00')")
    assert isinstance(result, _DT)
    assert result.year == 2024
    assert result.month == 3
    assert result.day == 15
    assert result.hour == 14


def test_parsedate_date_only():
    result = ev("parseDate('2024-06-01')")
    assert isinstance(result, _DT)
    assert result.year == 2024
    assert result.month == 6
    assert result.day == 1


def test_parsedate_z_suffix():
    result = ev("parseDate('2024-01-01T00:00:00Z')")
    assert isinstance(result, _DT)
    assert result.year == 2024


def test_parsedate_datetime_local_format():
    # HTML datetime-local inputs produce YYYY-MM-DDTHH:MM (no seconds, no tz)
    result = ev("parseDate('2024-03-15T14:30')")
    assert isinstance(result, _DT)
    assert result.year == 2024
    assert result.hour == 14
    assert result.minute == 30


def test_parsedate_datetime_with_seconds_no_tz():
    result = ev("parseDate('2024-03-15T14:30:00')")
    assert isinstance(result, _DT)
    assert result.second == 0


def test_to_datetime_via_datestart_with_time_no_tz():
    # Simulates a datetime-local value stored in a date property
    ctx = {"Event": {"start": "2024-06-15T09:00", "end": "2024-06-15T17:30"}}
    assert ev("hour(dateStart(prop('Event')))", ctx) == 9
    assert ev("hour(dateEnd(prop('Event')))", ctx) == 17


def test_datebetween_with_datetime_local_values():
    ctx = {
        "A": "2024-01-01T08:00",
        "B": "2024-01-01T10:30",
    }
    assert ev("dateBetween(prop('B'), prop('A'), 'hours')", ctx) == 2
    assert err("parseDate('not-a-date')")


def test_parsedate_non_string_returns_error():
    assert err("parseDate(42)")


# ─── Date extractors ──────────────────────────────────────────────────────────


def test_date_extractor():
    ctx = {"D": _ISO_DATE}
    assert ev("date(prop('D'))", ctx) == 15


def test_month_extractor():
    ctx = {"D": _ISO_DATE}
    assert ev("month(prop('D'))", ctx) == 3


def test_year_extractor():
    ctx = {"D": _ISO_DATE}
    assert ev("year(prop('D'))", ctx) == 2024


def test_hour_extractor():
    ctx = {"D": _ISO_DATE}
    assert ev("hour(prop('D'))", ctx) == 14


def test_minute_extractor():
    ctx = {"D": _ISO_DATE}
    assert ev("minute(prop('D'))", ctx) == 30


def test_day_of_week_friday():
    # 2024-03-15 is a Friday → 5 (Mon=1, Sun=7)
    ctx = {"D": _ISO_DATE}
    assert ev("day(prop('D'))", ctx) == 5


def test_day_of_week_monday():
    ctx = {"D": "2024-03-11T00:00:00+00:00"}
    assert ev("day(prop('D'))", ctx) == 1


def test_day_of_week_sunday():
    ctx = {"D": "2024-03-17T00:00:00+00:00"}
    assert ev("day(prop('D'))", ctx) == 7


def test_week_number():
    # 2024-03-15 is ISO week 11
    ctx = {"D": _ISO_DATE}
    assert ev("week(prop('D'))", ctx) == 11


def test_week_number_start_of_year():
    # 2024-01-01 is ISO week 1
    ctx = {"D": "2024-01-01T00:00:00+00:00"}
    assert ev("week(prop('D'))", ctx) == 1


# ─── dateAdd / dateSubtract ───────────────────────────────────────────────────


def test_dateadd_days():
    ctx = {"D": "2024-01-10T00:00:00+00:00"}
    result = ev("dateAdd(prop('D'), 5, 'days')", ctx)
    assert isinstance(result, _DT)
    assert result.day == 15


def test_dateadd_months():
    ctx = {"D": "2024-01-15T00:00:00+00:00"}
    result = ev("dateAdd(prop('D'), 2, 'months')", ctx)
    assert result.month == 3
    assert result.day == 15


def test_dateadd_months_clamps_to_end_of_month():
    # January 31 + 1 month → February 29 (2024 is a leap year)
    ctx = {"D": "2024-01-31T00:00:00+00:00"}
    result = ev("dateAdd(prop('D'), 1, 'months')", ctx)
    assert result.month == 2
    assert result.day == 29


def test_dateadd_years():
    ctx = {"D": "2024-03-15T00:00:00+00:00"}
    result = ev("dateAdd(prop('D'), 1, 'years')", ctx)
    assert result.year == 2025
    assert result.month == 3


def test_dateadd_weeks():
    ctx = {"D": "2024-01-01T00:00:00+00:00"}
    result = ev("dateAdd(prop('D'), 2, 'weeks')", ctx)
    assert result.day == 15


def test_dateadd_hours():
    ctx = {"D": "2024-01-01T10:00:00+00:00"}
    result = ev("dateAdd(prop('D'), 3, 'hours')", ctx)
    assert result.hour == 13


def test_dateadd_minutes():
    ctx = {"D": "2024-01-01T10:00:00+00:00"}
    result = ev("dateAdd(prop('D'), 90, 'minutes')", ctx)
    assert result.hour == 11
    assert result.minute == 30


def test_dateadd_quarters():
    ctx = {"D": "2024-01-15T00:00:00+00:00"}
    result = ev("dateAdd(prop('D'), 1, 'quarters')", ctx)
    assert result.month == 4


def test_datesubtract_days():
    ctx = {"D": "2024-01-15T00:00:00+00:00"}
    result = ev("dateSubtract(prop('D'), 5, 'days')", ctx)
    assert result.day == 10


def test_datesubtract_months():
    ctx = {"D": "2024-03-15T00:00:00+00:00"}
    result = ev("dateSubtract(prop('D'), 1, 'months')", ctx)
    assert result.month == 2


def test_dateadd_singular_unit():
    # "day" (singular) should be accepted as well as "days"
    ctx = {"D": "2024-01-10T00:00:00+00:00"}
    result = ev("dateAdd(prop('D'), 1, 'day')", ctx)
    assert result.day == 11


def test_dateadd_unknown_unit_returns_error():
    ctx = {"D": "2024-01-10T00:00:00+00:00"}
    assert err("dateAdd(prop('D'), 1, 'fortnights')", ctx)


# ─── dateBetween ──────────────────────────────────────────────────────────────


def test_datebetween_days_positive():
    ctx = {
        "A": "2024-01-01T00:00:00+00:00",
        "B": "2024-01-11T00:00:00+00:00",
    }
    assert ev("dateBetween(prop('B'), prop('A'), 'days')", ctx) == 10


def test_datebetween_days_negative():
    ctx = {
        "A": "2024-01-01T00:00:00+00:00",
        "B": "2024-01-11T00:00:00+00:00",
    }
    assert ev("dateBetween(prop('A'), prop('B'), 'days')", ctx) == -10


def test_datebetween_weeks():
    ctx = {
        "A": "2024-01-01T00:00:00+00:00",
        "B": "2024-01-22T00:00:00+00:00",
    }
    assert ev("dateBetween(prop('B'), prop('A'), 'weeks')", ctx) == 3


def test_datebetween_months():
    ctx = {
        "A": "2024-01-01T00:00:00+00:00",
        "B": "2024-04-01T00:00:00+00:00",
    }
    assert ev("dateBetween(prop('B'), prop('A'), 'months')", ctx) == 3


def test_datebetween_years():
    ctx = {
        "A": "2020-06-15T00:00:00+00:00",
        "B": "2024-06-15T00:00:00+00:00",
    }
    assert ev("dateBetween(prop('B'), prop('A'), 'years')", ctx) == 4


def test_datebetween_hours():
    ctx = {
        "A": "2024-01-01T00:00:00+00:00",
        "B": "2024-01-01T06:00:00+00:00",
    }
    assert ev("dateBetween(prop('B'), prop('A'), 'hours')", ctx) == 6


def test_datebetween_same_date_is_zero():
    ctx = {"D": "2024-01-01T00:00:00+00:00"}
    assert ev("dateBetween(prop('D'), prop('D'), 'days')", ctx) == 0


# ─── formatDate ───────────────────────────────────────────────────────────────

_FMT_CTX = {"D": "2024-03-15T14:05:09+00:00"}  # Friday, week 11


def test_formatdate_ddmmyyyy():
    assert ev("formatDate(prop('D'), 'DD-MM-YYYY')", _FMT_CTX) == "15-03-2024"


def test_formatdate_iso_style():
    assert ev("formatDate(prop('D'), 'YYYY-MM-DD')", _FMT_CTX) == "2024-03-15"


def test_formatdate_full_month_name():
    assert ev("formatDate(prop('D'), 'MMMM YYYY')", _FMT_CTX) == "March 2024"


def test_formatdate_abbreviated_month():
    assert ev("formatDate(prop('D'), 'MMM DD, YYYY')", _FMT_CTX) == "Mar 15, 2024"


def test_formatdate_weekday_full():
    assert ev("formatDate(prop('D'), 'DDDD')", _FMT_CTX) == "Friday"


def test_formatdate_weekday_short():
    assert ev("formatDate(prop('D'), 'DDD')", _FMT_CTX) == "Fri"


def test_formatdate_time_24h():
    assert ev("formatDate(prop('D'), 'HH:mm')", _FMT_CTX) == "14:05"


def test_formatdate_time_12h():
    assert ev("formatDate(prop('D'), 'h:mm A')", _FMT_CTX) == "2:05 PM"


def test_formatdate_two_digit_year():
    assert ev("formatDate(prop('D'), 'YY')", _FMT_CTX) == "24"


def test_formatdate_unpadded_month():
    assert ev("formatDate(prop('D'), 'M')", _FMT_CTX) == "3"


def test_formatdate_unpadded_day():
    assert ev("formatDate(prop('D'), 'D')", _FMT_CTX) == "15"


def test_formatdate_seconds():
    assert ev("formatDate(prop('D'), 'ss')", _FMT_CTX) == "09"


def test_formatdate_am_lowercase():
    ctx = {"D": "2024-03-15T08:00:00+00:00"}
    assert ev("formatDate(prop('D'), 'h:mm a')", ctx) == "8:00 am"


def test_formatdate_non_date_returns_error():
    assert err("formatDate('not-a-date', 'YYYY')")


# ─── dateStart / dateEnd ─────────────────────────────────────────────────────

# Date property dicts as they arrive from computed._extract_scalar
_DATE_RANGE_CTX = {
    "Event": {"start": "2024-03-15T10:00:00+00:00", "end": "2024-03-15T18:00:00+00:00"},
    "SingleDay": {"start": "2024-06-01T00:00:00+00:00", "end": None},
}


def test_datestart_from_date_dict():
    result = ev("dateStart(prop('Event'))", _DATE_RANGE_CTX)
    assert isinstance(result, _DT)
    assert result.year == 2024
    assert result.month == 3
    assert result.day == 15
    assert result.hour == 10


def test_dateend_from_date_dict():
    result = ev("dateEnd(prop('Event'))", _DATE_RANGE_CTX)
    assert isinstance(result, _DT)
    assert result.hour == 18


def test_dateend_no_end_returns_none():
    result = ev("dateEnd(prop('SingleDay'))", _DATE_RANGE_CTX)
    assert result is None


def test_datestart_extracts_year():
    result = ev("year(dateStart(prop('Event')))", _DATE_RANGE_CTX)
    assert result == 2024


def test_dateend_extracts_hour():
    result = ev("hour(dateEnd(prop('Event')))", _DATE_RANGE_CTX)
    assert result == 18


def test_datebetween_start_to_end():
    result = ev("dateBetween(dateEnd(prop('Event')), dateStart(prop('Event')), 'hours')",
                _DATE_RANGE_CTX)
    assert result == 8


def test_datestart_plain_string_passthrough():
    # dateStart on a plain ISO string behaves like parseDate
    ctx = {"D": "2024-06-01T00:00:00+00:00"}
    result = ev("dateStart(prop('D'))", ctx)
    assert isinstance(result, _DT)
    assert result.day == 1


def test_dateend_plain_string_returns_none():
    # A plain string has no "end" — dateEnd returns None
    ctx = {"D": "2024-06-01T00:00:00+00:00"}
    result = ev("dateEnd(prop('D'))", ctx)
    assert result is None


def test_datestart_plain_datetime_object():
    dt = _DT(2024, 6, 15, 9, 0, 0, tzinfo=timezone.utc)
    result = ev("dateStart(prop('D'))", {"D": dt})
    assert result.day == 15


def test_dateend_wrong_arg_count():
    assert err("dateEnd(now(), now())")


def test_datestart_wrong_arg_count():
    assert err("dateStart()")


def test_datestart_null_returns_error():
    assert err("dateStart(null)")


def test_dateend_date_dict_no_start_returns_error():
    ctx = {"D": {"start": None, "end": None}}
    assert err("dateStart(prop('D'))", ctx)


def test_formatdate_with_datestart():
    result = ev("formatDate(dateStart(prop('Event')), 'HH:mm')", _DATE_RANGE_CTX)
    assert result == "10:00"


def test_formatdate_with_dateend():
    result = ev("formatDate(dateEnd(prop('Event')), 'HH:mm')", _DATE_RANGE_CTX)
    assert result == "18:00"


def test_existing_date_functions_still_work_with_dict():
    # _to_datetime now accepts dicts — make sure month/year/dateAdd still work
    # when called on a date property dict (fallback: uses "start")
    ctx = {"Event": {"start": "2024-06-15T00:00:00+00:00", "end": "2024-06-20T00:00:00+00:00"}}
    assert ev("year(prop('Event'))", ctx) == 2024
    assert ev("month(prop('Event'))", ctx) == 6
    result = ev("dateAdd(prop('Event'), 1, 'days')", ctx)
    assert isinstance(result, _DT)
    assert result.day == 16


def test_datetime_object_in_context():
    dt = _DT(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    result = ev("year(prop('D'))", {"D": dt})
    assert result == 2024


def test_naive_datetime_assumed_utc():
    dt = _DT(2024, 6, 15, 12, 0, 0)  # no tzinfo
    result = ev("month(prop('D'))", {"D": dt})
    assert result == 6


# ─── Argument count errors ────────────────────────────────────────────────────


def test_prop_too_many_args():
    assert err("prop('a', 'b')")


def test_if_wrong_args():
    assert err("if(true, 1)")


def test_abs_no_args():
    assert err("abs()")


def test_len_non_string():
    assert err("len(42)")


def test_now_with_args_raises():
    assert err("now(42)")


def test_today_with_args_raises():
    assert err("today(42)")


def test_dateadd_wrong_arg_count():
    assert err("dateAdd(now(), 1)")


def test_datesubtract_wrong_arg_count():
    assert err("dateSubtract(now())")


def test_datebetween_wrong_arg_count():
    assert err("dateBetween(now(), now())")


def test_formatdate_wrong_arg_count():
    assert err("formatDate(now())")


def test_date_extractor_wrong_arg_count():
    assert err("year(now(), 'extra')")


# ─── Syntax errors ────────────────────────────────────────────────────────────


def test_unterminated_string():
    with pytest.raises(FormulaError):
        validate_syntax("'oops")


def test_unexpected_char():
    with pytest.raises(FormulaError):
        validate_syntax("1 @ 2")


def test_bare_identifier_raises():
    result = evaluate("x", {})
    assert result.error is not None


def test_unclosed_paren():
    result = evaluate("(1 + 2", {})
    assert result.error is not None


def test_empty_expression():
    result = evaluate("", {})
    assert result.error is not None


def test_validate_syntax_valid():
    validate_syntax("prop('A') * 2 + 1")  # must not raise


def test_validate_syntax_invalid():
    with pytest.raises(FormulaError):
        validate_syntax("1 +")


def test_validate_syntax_unknown_function_raises():
    # Before the fix, validate_syntax accepted any identifier as a function
    # call without checking whether it was actually implemented.  Now it must
    # reject unknown names so the modal shows an error at edit time.
    with pytest.raises(FormulaError, match="Unknown function"):
        validate_syntax("unknownFn(prop('X'))")


def test_validate_syntax_empty_is_now_known():
    # empty() was the concrete function that triggered the bug.  After the fix
    # it must pass validation without raising.
    validate_syntax("empty(prop('Value'))")


# ─── extract_prop_names ───────────────────────────────────────────────────────


def test_extract_single_prop():
    assert extract_prop_names("prop('Price')") == ["Price"]


def test_extract_multiple_props():
    assert extract_prop_names("prop('A') + prop('B')") == ["A", "B"]


def test_extract_deduplicates():
    assert extract_prop_names("prop('X') + prop('X')") == ["X"]


def test_extract_nested_in_if():
    names = extract_prop_names("if(prop('Flag'), prop('A'), prop('B'))")
    assert set(names) == {"Flag", "A", "B"}


def test_extract_no_props():
    assert extract_prop_names("1 + 2 * 3") == []


def test_extract_syntax_error_raises():
    with pytest.raises(FormulaError):
        extract_prop_names("'unterminated")


# ─── Complex / integration ────────────────────────────────────────────────────


def test_complex_price_vat():
    result = ev("round(prop('Price') * 1.19, 2)", {"Price": 100})
    assert result == pytest.approx(119.0)


def test_conditional_with_nested_arithmetic():
    ctx = {"Score": 75}
    assert ev("if(prop('Score') >= 90, 'A', if(prop('Score') >= 60, 'B', 'C'))", ctx) == "B"


def test_power_and_arithmetic():
    assert ev("2 ^ 3 + 1") == 9  # (2^3) + 1


def test_string_comparison():
    assert ev('"abc" == "abc"') is True
    assert ev('"abc" != "def"') is True


def test_empty_prop_treated_as_zero_in_arithmetic():
    # An empty (missing) source property counts as 0 in a numeric context,
    # so the formula yields a value instead of surfacing a type error.
    result = evaluate("prop('Missing') + 1", {})
    assert result.error is None
    assert result.result == 1


def test_ifs_inside_formula():
    ctx = {"Days": 45}
    result = ev(
        "concat('Status: ', ifs("
        "  prop('Days') > 30, 'Overdue',"
        "  prop('Days') > 14, 'Due soon',"
        "  'OK'"
        "))",
        ctx,
    )
    assert result == "Status: Overdue"


def test_date_arithmetic_pipeline():
    # Days until a deadline
    ctx = {
        "Deadline": "2024-06-30T00:00:00+00:00",
        "Start": "2024-06-01T00:00:00+00:00",
    }
    assert ev("dateBetween(prop('Deadline'), prop('Start'), 'days')", ctx) == 29


def test_formatdate_with_dateadd():
    ctx = {"D": "2024-01-01T00:00:00+00:00"}
    result = ev("formatDate(dateAdd(prop('D'), 1, 'months'), 'YYYY-MM-DD')", ctx)
    assert result == "2024-02-01"


# ─── contains() ──────────────────────────────────────────────────────────────


def test_contains_basic():
    assert ev("contains('hello world', 'world')") is True


def test_contains_not_found():
    assert ev("contains('hello world', 'xyz')") is False


def test_contains_case_sensitive():
    assert ev("contains('Hello', 'hello')") is False


def test_contains_empty_substring():
    assert ev("contains('hello', '')") is True


def test_contains_prop():
    assert ev("contains(prop('Name'), 'Giro')", {"Name": "1200 Giro Volksbank"}) is True


def test_contains_none_text_returns_false():
    assert ev("contains(prop('Missing'), 'x')", {}) is False


def test_contains_none_sub_returns_false():
    assert ev("contains('hello', prop('Missing'))", {}) is False


def test_contains_wrong_arg_count():
    assert err("contains('a')")
    assert err("contains('a', 'b', 'c')")


# ─── equal() ─────────────────────────────────────────────────────────────────


def test_equal_numbers_true():
    assert ev("equal(1, 1)") is True


def test_equal_numbers_false():
    assert ev("equal(1, 2)") is False


def test_equal_strings():
    assert ev("equal('abc', 'abc')") is True


def test_equal_wrong_arg_count():
    assert err("equal(1)")
    assert err("equal(1, 2, 3)")


# ─── divide() ────────────────────────────────────────────────────────────────


def test_divide_basic():
    assert ev("divide(10, 4)") == pytest.approx(2.5)


def test_divide_by_zero_raises():
    assert err("divide(1, 0)")


def test_divide_wrong_arg_count():
    assert err("divide(1)")


# ─── style() / unstyle() ─────────────────────────────────────────────────────


def test_style_returns_value():
    result = evaluate("style(42, 'red')", {})
    assert result.result == 42
    assert result.error is None


def test_style_carries_hints():
    result = evaluate("style(42, 'red')", {})
    assert result.style == ["red"]


def test_style_multiple_hints():
    result = evaluate("style('hello', 'b', 'blue')", {})
    assert result.result == "hello"
    assert result.style == ["b", "blue"]


def test_style_in_if_branch():
    ctx = {"Score": -5}
    result = evaluate("if(prop('Score') < 0, style(prop('Score'), 'red'), prop('Score'))", ctx)
    assert result.result == -5
    assert result.style == ["red"]


def test_style_positive_branch_no_style():
    ctx = {"Score": 10}
    result = evaluate("if(prop('Score') < 0, style(prop('Score'), 'red'), prop('Score'))", ctx)
    assert result.result == 10
    assert result.style == []


def test_style_result_participates_in_arithmetic():
    # styled value used inside a larger expression: style is dropped, value survives
    result = evaluate("style(3, 'red') + 7", {})
    assert result.result == 10
    assert result.style == []


def test_style_nested_merges():
    result = evaluate("style(style(1, 'b'), 'red')", {})
    assert result.result == 1
    assert result.style == ["b", "red"]


def test_unstyle_removes_style():
    result = evaluate("unstyle(style(42, 'red'))", {})
    assert result.result == 42
    assert result.style == []


def test_unstyle_plain_value_passthrough():
    result = evaluate("unstyle(42)", {})
    assert result.result == 42
    assert result.style == []


def test_style_wrong_arg_count():
    assert err("style(42)")


def test_unstyle_wrong_arg_count():
    assert err("unstyle()")
    assert err("unstyle(1, 2)")


# ─── Datetime comparison operators ────────────────────────────────────────────


def test_date_lt_string_comparison():
    ctx = {
        "Start": "2024-01-01",
        "End": "2024-12-31",
        "Date": "2024-06-15",
    }
    assert ev("prop('Start') < prop('Date')", ctx) is True
    assert ev("prop('Date') < prop('End')", ctx) is True


def test_date_lte_equal_dates():
    ctx = {"A": "2024-06-15", "B": "2024-06-15"}
    assert ev("prop('A') <= prop('B')", ctx) is True
    assert ev("prop('A') < prop('B')", ctx) is False


def test_date_gt_string_comparison():
    ctx = {"Later": "2025-01-01", "Earlier": "2024-01-01"}
    assert ev("prop('Later') > prop('Earlier')", ctx) is True


def test_date_gte_string_comparison():
    ctx = {"A": "2024-06-15", "B": "2024-06-15"}
    assert ev("prop('A') >= prop('B')", ctx) is True


def test_date_comparison_with_datetime_string():
    ctx = {
        "Start": "2024-01-01T00:00:00+00:00",
        "Booking": "2024-06-15T00:00:00+00:00",
        "End": "2024-12-31T23:59:59+00:00",
    }
    assert ev("prop('Start') <= prop('Booking') and prop('Booking') <= prop('End')", ctx) is True


def test_date_comparison_with_dict_date_property():
    # Date properties arrive as {"start": ..., "end": ...} dicts
    ctx = {
        "Start": "2024-01-01T00:00:00+00:00",
        "Buchungsdatum": {"start": "2024-06-15", "end": None},
        "End": "2024-12-31T23:59:59+00:00",
    }
    assert ev(
        "prop('Start') <= prop('Buchungsdatum') and prop('Buchungsdatum') <= prop('End')",
        ctx,
    ) is True


def test_date_comparison_outside_range():
    ctx = {
        "Start": "2024-01-01",
        "Booking": "2023-12-31",
        "End": "2024-12-31",
    }
    assert ev("prop('Start') <= prop('Booking') and prop('Booking') <= prop('End')", ctx) is False


def test_numeric_lt_still_works():
    # Ensure numeric comparison is not broken by the datetime fast-path
    assert ev("3 < 5") is True
    assert ev("5 < 3") is False


def test_numeric_lte_still_works():
    assert ev("3 <= 3") is True
    assert ev("4 <= 3") is False


def test_validate_syntax_accepts_new_functions():
    validate_syntax("contains(prop('Name'), 'x')")
    validate_syntax("style(prop('Amount'), 'red')")
    validate_syntax("unstyle(prop('X'))")
    validate_syntax("equal(prop('A'), prop('B'))")
    validate_syntax("divide(prop('A'), prop('B'))")

# ─── Empty source coercion (empty == 0) ──────────────────────────────────────
#
# Regression coverage for the bug where a formula cell rendered "Error" when a
# referenced source property was empty. In a numeric context an empty value
# (None or "") is treated as 0, matching Notion. The variadic aggregates skip
# empties instead, and toNumber("") deliberately stays empty (None).


def test_empty_add_yields_other_operand():
    # prop a + prop b, b empty -> a
    assert ev("prop('a') + prop('b')", {"a": 5}) == 5
    assert ev("prop('b') + prop('a')", {"a": 5}) == 5


def test_empty_add_both_empty_yields_empty():
    # All operands empty -> relay empty (None), not a fabricated 0.
    assert ev("prop('a') + prop('b')", {}) is None


def test_empty_subtract():
    assert ev("prop('a') - prop('b')", {"a": 5}) == 5
    assert ev("prop('a') - prop('b')", {"b": 5}) == -5


def test_empty_multiply_yields_zero():
    assert ev("prop('a') * prop('b')", {"a": 5}) == 0


def test_empty_power_base_and_exponent():
    assert ev("prop('a') ^ prop('b')", {"a": 5}) == 1   # 5 ^ 0
    assert ev("prop('a') ^ prop('b')", {"b": 3}) == 0   # 0 ^ 3


def test_empty_negation_yields_empty():
    assert ev("-prop('b')", {}) is None


def test_empty_in_unary_math_functions_yields_empty():
    assert ev("abs(prop('b'))", {}) is None
    assert ev("round(prop('b'))", {}) is None
    assert ev("ceil(prop('b'))", {}) is None
    assert ev("floor(prop('b'))", {}) is None


def test_empty_string_in_numeric_context_is_zero():
    # An empty text property ("") used outside the string-concat (+) path
    assert ev("prop('t') * 2", {"t": ""}) == 0
    assert ev("prop('t') - 3", {"t": ""}) == -3


def test_empty_in_ordered_comparison():
    assert ev("prop('b') < prop('a')", {"a": 5}) is True    # 0 < 5
    assert ev("prop('a') >= prop('b')", {"a": 5}) is True   # 5 >= 0
    assert ev("prop('t') < 5", {"t": ""}) is True           # 0 < 5


def test_genuine_non_numeric_string_still_errors():
    # Only *empty* coerces to 0; real garbage must still raise.
    assert err("prop('t') * 2", {"t": "abc"})


def test_empty_divisor_is_division_by_zero():
    # empty -> 0, so dividing by it is a genuine division-by-zero error.
    assert "Division by zero" in err("prop('a') / prop('b')", {"a": 5})
    assert "Division by zero" in err("10 / prop('b')", {})


def test_empty_modulo_divisor_is_modulo_by_zero():
    assert "Modulo by zero" in err("prop('a') % prop('b')", {"a": 5})


def test_dateadd_with_empty_amount_leaves_date_unchanged():
    ctx = {"D": "2024-06-15T00:00:00+00:00"}
    assert ev(
        "formatDate(dateAdd(prop('D'), prop('n'), 'days'), 'YYYY-MM-DD')", ctx
    ) == "2024-06-15"


def test_variadic_skips_empty_string_like_none():
    # sum/min/max/avg exclude empties so they do not skew the aggregate.
    assert ev("sum(prop('a'), prop('b'))", {"a": 5}) == 5
    assert ev("sum(prop('a'), prop('t'))", {"a": 5, "t": ""}) == 5
    assert ev("min(prop('a'), prop('b'))", {"a": 5}) == 5      # not 0
    assert ev("avg(prop('a'), prop('b'))", {"a": 4}) == 4      # empty excluded
    assert ev("avg(prop('a'), prop('t'))", {"a": 4, "t": ""}) == 4


def test_variadic_all_empty_returns_none():
    assert ev("sum(prop('a'), prop('b'))", {}) is None
    assert ev("avg(prop('a'), prop('t'))", {"t": ""}) is None


def test_tonumber_empty_stays_empty_but_integrates_with_arithmetic():
    # Standalone toNumber of an empty source relays empty (Notion idiom)…
    assert ev("toNumber(prop('x'))", {}) is None
    # …yet still composes in arithmetic because + coerces the None to 0.
    assert ev("toNumber(prop('x')) + 5", {}) == 5

# ─── All-source-empty relays empty (not 0) ───────────────────────────────────
#
# When *every* operand of an arithmetic operation is empty there is nothing to
# compute, so the result is relayed as empty. A single empty among concrete
# values still counts as 0 (covered above). Empty sub-results propagate, so a
# fully empty nested expression stays empty while any concrete value (prop or
# literal) collapses the empties around it to 0.


def test_all_empty_subtract_multiply_power_yield_empty():
    assert ev("prop('a') - prop('b')", {}) is None
    assert ev("prop('a') * prop('b')", {}) is None
    assert ev("prop('a') ^ prop('b')", {}) is None


def test_all_empty_divide_relays_empty_not_div_by_zero():
    # Both empty -> empty, rather than coercing the divisor to 0 and erroring.
    assert ev("prop('a') / prop('b')", {}) is None
    assert ev("prop('a') % prop('b')", {}) is None
    assert ev("divide(prop('a'), prop('b'))", {}) is None


def test_empty_propagates_through_nested_arithmetic():
    # (a + b) + c, all empty -> empty all the way up.
    assert ev("prop('a') + prop('b') + prop('c')", {}) is None
    assert ev("(prop('a') - prop('b')) * prop('c')", {}) is None


def test_concrete_value_collapses_surrounding_empties_to_zero():
    # A literal counts as concrete, so the empties around it act as 0.
    assert ev("prop('a') + prop('b') + 5", {}) == 5
    assert ev("prop('a') + prop('b')", {"b": 7}) == 7
    # A concrete 0 (not empty) is distinct from empty and keeps computing.
    assert ev("prop('a') + prop('b')", {"a": 0, "b": 0}) == 0


def test_single_empty_unary_and_math_still_zero_when_concrete():
    # Contrast with the all-empty unary tests: a concrete value computes.
    assert ev("-prop('b')", {"b": 4}) == -4
    assert ev("abs(prop('b'))", {"b": -3}) == 3
    assert ev("round(prop('b'), 1)", {"b": 2.34}) == pytest.approx(2.3)


# ─── at() ─────────────────────────────────────────────────────────────────────


def test_at_first_element():
    assert ev("at(prop('L'), 0)", {"L": ["a", "b", "c"]}) == "a"


def test_at_middle_element():
    assert ev("at(prop('L'), 1)", {"L": ["a", "b", "c"]}) == "b"


def test_at_last_element_by_positive_index():
    assert ev("at(prop('L'), 2)", {"L": ["a", "b", "c"]}) == "c"


def test_at_negative_index_last():
    assert ev("at(prop('L'), -1)", {"L": ["a", "b", "c"]}) == "c"


def test_at_negative_index_second_to_last():
    assert ev("at(prop('L'), -2)", {"L": ["a", "b", "c"]}) == "b"


def test_at_out_of_bounds_positive():
    assert ev("at(prop('L'), 99)", {"L": ["a", "b"]}) is None


def test_at_out_of_bounds_negative():
    assert ev("at(prop('L'), -99)", {"L": ["a", "b"]}) is None


def test_at_empty_list_returns_none():
    assert ev("at(prop('L'), 0)", {"L": []}) is None


def test_at_null_list_returns_none():
    assert ev("at(null, 0)") is None


def test_at_null_index_returns_none():
    assert ev("at(prop('L'), null)", {"L": ["x"]}) is None


def test_at_numeric_elements():
    assert ev("at(prop('L'), 0)", {"L": [10, 20, 30]}) == 10


def test_at_with_none_element():
    # None is a valid element; at() returns it as-is
    assert ev("at(prop('L'), 1)", {"L": ["a", None, "c"]}) is None


def test_at_date_string_element():
    # Primary use-case from issue #13: index into a rollup date list
    result = ev("at(prop('Dates'), 0)", {"Dates": ["2024-06-15", "2025-01-01"]})
    assert result == "2024-06-15"


def test_at_date_string_usable_in_datebetween():
    from datetime import datetime as _DT, timezone
    ctx = {
        "Datum": _DT(2026, 1, 1, tzinfo=timezone.utc),
        "Rollup": ["2000-01-01"],
    }
    result = ev("dateBetween(prop('Datum'), at(prop('Rollup'), 0), 'years')", ctx)
    assert result == 26


def test_at_wrong_arg_count_zero():
    assert err("at()")


def test_at_wrong_arg_count_one():
    assert err("at(prop('L'))")


def test_at_wrong_arg_count_three():
    assert err("at(prop('L'), 0, 1)")


def test_at_non_list_first_arg_raises():
    assert err("at('hello', 0)")


def test_at_validate_syntax_accepted():
    validate_syntax("at(prop('Dates'), 0)")

# ─── rename_prop_in_expression ────────────────────────────────────────────────


def test_rename_prop_basic_double_quote():
    assert rename_prop_in_expression('prop("Old") + 1', "Old", "New") == 'prop("New") + 1'


def test_rename_prop_basic_single_quote_preserved():
    assert rename_prop_in_expression("prop('Old') + 1", "Old", "New") == "prop('New') + 1"


def test_rename_prop_multiple_occurrences():
    expr = 'prop("Old") + prop("Old") * prop("Other")'
    assert (
        rename_prop_in_expression(expr, "Old", "New")
        == 'prop("New") + prop("New") * prop("Other")'
    )


def test_rename_prop_leaves_unrelated_string_literals_untouched():
    # A compared value that merely equals the old name must NOT be rewritten —
    # only the prop() argument is. This is the whole point of token-based rename.
    expr = 'if(prop("Status") == "Status", "Status", prop("Other"))'
    assert (
        rename_prop_in_expression(expr, "Status", "Phase")
        == 'if(prop("Phase") == "Status", "Status", prop("Other"))'
    )


def test_rename_prop_no_match_returns_unchanged():
    expr = 'prop("Other") + 1'
    assert rename_prop_in_expression(expr, "Old", "New") == expr


def test_rename_prop_identity_when_names_equal():
    expr = 'prop("Name")'
    assert rename_prop_in_expression(expr, "Name", "Name") == expr


def test_rename_prop_preserves_surrounding_formatting():
    expr = "if(  prop('Old')  >= 45 , 'x', 'y' )"
    assert (
        rename_prop_in_expression(expr, "Old", "New")
        == "if(  prop('New')  >= 45 , 'x', 'y' )"
    )


def test_rename_prop_name_with_spaces_and_parens():
    expr = 'prop("Geschlecht") == "Weiblich"'
    assert (
        rename_prop_in_expression(expr, "Geschlecht", "Geschlecht (Biologisch)")
        == 'prop("Geschlecht (Biologisch)") == "Weiblich"'
    )


def test_rename_prop_new_name_with_quote_is_escaped_and_relexes():
    # A new name containing the same quote char must be escaped so the result
    # still lexes back to that exact name.
    out = rename_prop_in_expression('prop("Old")', "Old", 'A "B" C')
    assert extract_prop_names(out) == ['A "B" C']


def test_rename_prop_malformed_expression_returned_unchanged():
    # An expression that no longer lexes (unterminated string) is left as-is so
    # a rename never fails on a pre-existing broken formula.
    expr = 'prop("Old'
    assert rename_prop_in_expression(expr, "Old", "New") == expr


def test_rename_prop_empty_expression():
    assert rename_prop_in_expression("", "Old", "New") == ""


def test_rename_prop_result_still_evaluates():
    # End-to-end: after rename the formula resolves against the new context key.
    out = rename_prop_in_expression("prop('Old') + 5", "Old", "New")
    assert ev(out, {"New": 10}) == 15
