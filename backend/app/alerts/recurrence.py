"""
Repeating dates.

A date property value may repeat. The fields live in the value next to
``start`` and ``end``:

    repeat           'none' | 'daily' | 'weekly' | 'monthly' | 'yearly'
    repeatInterval   positive integer, default 1
    repeatUntil      YYYY-MM-DD, the last day an occurrence may start on
    repeatExceptions YYYY-MM-DD dates of occurrences that were removed

The calendar expands them in the browser (``frontend/src/composables/
recurrenceUtils.ts``); this module gives the server the same occurrences,
which the system alerts of calendar dates need. The rules match the frontend
exactly, including how a month or year step overflows: 31 January plus one
month is 3 March (2 March in a leap year), 29 February plus one year is
1 March, because that is what the browser's Date arithmetic does.

Occurrence ``n`` is always computed from the first date, never from the one
before, so an overflow does not carry over into later occurrences.
"""
from datetime import date, timedelta
from typing import Iterator, Optional

# Steps of a repeat mode, in days for the fixed ones.
_DAYS = {"daily": 1, "weekly": 7}
_MONTHS = {"monthly": 1, "yearly": 12}

# Most occurrences looked at after the starting point; generous for any
# realistic series, and a bound against malformed values.
MAX_OCCURRENCES = 2000


def parse_date(text: object) -> Optional[date]:
    """A ``YYYY-MM-DD`` prefix as a date, or None."""
    if not isinstance(text, str) or len(text) < 8:
        return None
    try:
        year, month, day = text[:10].split("T")[0].split("-")
        return date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None


def _add_months(start: date, months: int) -> Optional[date]:
    """*start* moved by *months*, overflowing into the next month like JavaScript."""
    total = start.year * 12 + (start.month - 1) + months
    year, month0 = divmod(total, 12)
    try:
        return date(year, month0 + 1, 1) + timedelta(days=start.day - 1)
    except (ValueError, OverflowError):
        return None


def nth_occurrence(start: date, mode: str, step: int) -> Optional[date]:
    """The date *step* repeat units after *start*."""
    try:
        if mode in _DAYS:
            return start + timedelta(days=_DAYS[mode] * step)
        if mode in _MONTHS:
            return _add_months(start, _MONTHS[mode] * step)
    except (ValueError, OverflowError):
        return None
    return None


def _first_step_near(start: date, mode: str, interval: int, target: date) -> int:
    """A step index a little before *target*, so iteration need not start at 0."""
    if target <= start:
        return 0
    if mode in _DAYS:
        units = (target - start).days // _DAYS[mode]
    else:
        units = ((target.year - start.year) * 12 + target.month - start.month) // _MONTHS[mode]
    return max(0, units // interval - 1)


def occurrences(value: dict, first: date, from_date: Optional[date] = None) -> Iterator[date]:
    """
    The start dates of *value*'s occurrences in order, beginning with *first*
    (the value's own start date) or, with *from_date*, shortly before it.
    A value that does not repeat yields *first* once.
    """
    mode = value.get("repeat") or "none"
    if mode not in _DAYS and mode not in _MONTHS:
        yield first
        return
    try:
        interval = max(1, int(value.get("repeatInterval") or 1))
    except (TypeError, ValueError):
        interval = 1
    until = parse_date(value.get("repeatUntil"))
    exceptions = {
        d for d in (parse_date(x) for x in (value.get("repeatExceptions") or [])) if d
    }
    n = _first_step_near(first, mode, interval, from_date) if from_date else 0
    for _ in range(MAX_OCCURRENCES):
        current = nth_occurrence(first, mode, n * interval)
        n += 1
        if current is None:
            return
        if until is not None and current > until:
            return
        if current in exceptions:
            continue
        yield current
