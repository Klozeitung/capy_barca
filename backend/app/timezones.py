"""
Time zones and the storage of times.

Storage
-------
Times of day are stored as instants in UTC; the frontend and the Android app
never see that. Every value of a ``date`` property that carries a time of
day is kept as ``YYYY-MM-DDTHH:MM:SSZ``. The ``Z`` is what tells a converted
value from one written before times were stored in UTC, so the one-off
conversion (``app.maintenance.convert_dates_to_utc``) can run any number of
times without shifting anything twice.

Dates without a time of day (all-day values) stay plain calendar dates,
``YYYY-MM-DD``, without a zone, as iCalendar keeps them: the 17th is the 17th
everywhere. Period keys of timelines stay as written, too; they label periods
rather than name instants.

Boundary
--------
The API speaks local time. Values arriving from a client are read in the
zone of the request (:func:`zone_of`) and stored in UTC
(:func:`date_value_to_utc`); values leaving the server are turned back into
that zone (:func:`date_value_to_local`). A value without ``Z`` is taken as
local time as it is, so data written before the conversion reads unchanged.

Daylight saving time is resolved for the date of the value itself, not for
today: 09:00 on an August day is 07:00 UTC even when it is entered in
December. A local time that does not exist (the hour skipped in spring) is
read with the offset before the change, which moves it forward by that hour;
one that exists twice (autumn) is taken as its first occurrence. Both follow
RFC 5545.

Zones
-----
* The zone of a request: the ``X-Timezone`` header the web app sends
  (remembered by ``get_db``), else the account's ``timezone`` (remembered by
  ``get_current_user``), else the installation's.
* The installation's zone: ``CB_TIMEZONE`` in ``.env`` (``setup.sh`` takes it
  from the host), else UTC. Values computed once for everybody, formulas and
  rollups, use it.
"""
import os
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Request header carrying the client's IANA time zone.
TIMEZONE_HEADER = "X-Timezone"

# Environment variable naming the installation's zone.
INSTALLATION_ZONE_ENV = "CB_TIMEZONE"

# Keys under which a database session remembers the zone its request named
# and the zone of the account it acts for.
_HEADER_ZONE_KEY = "capybarca_header_zone"
_USER_ZONE_KEY = "capybarca_user_zone"

# A time of day: local "YYYY-MM-DDTHH:MM[:SS]" or UTC "…Z". The year may be
# shorter than four digits in data written before years were padded.
_TIMED_RE = re.compile(
    r"^(\d{1,4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?"
    r"(Z|[+-]\d{2}:\d{2})?$"
)

UTC = timezone.utc


# ─── Zones ────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=256)
def _load(name: str) -> Optional[ZoneInfo]:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return None


def parse_zone(name: Optional[str]) -> Optional[ZoneInfo]:
    """The zone *name* as an IANA identifier, or None when it is not one."""
    if not name or not isinstance(name, str) or len(name) > 64:
        return None
    return _load(name.strip())


def installation_zone() -> ZoneInfo:
    """The installation's zone (``CB_TIMEZONE``), UTC when unset or unknown."""
    return parse_zone(os.getenv(INSTALLATION_ZONE_ENV)) or ZoneInfo("UTC")


def user_zone(user: Any) -> ZoneInfo:
    """The zone of an account, the installation's when it has none."""
    return parse_zone(getattr(user, "timezone", None)) or installation_zone()


def set_header_zone(db: Any, header_value: Optional[str]) -> None:
    """Remember the zone a request names in its header, if it is one."""
    zone = parse_zone(header_value)
    if zone is not None:
        db.info[_HEADER_ZONE_KEY] = zone


def set_user_zone(db: Any, user: Any) -> None:
    """Remember the zone of the account a request acts for, if it has one."""
    zone = parse_zone(getattr(user, "timezone", None))
    if zone is not None:
        db.info[_USER_ZONE_KEY] = zone


def zone_of(db: Any) -> ZoneInfo:
    """
    The zone of the request *db* serves: the header's, else the account's,
    else the installation's.
    """
    info = getattr(db, "info", None)
    if isinstance(info, dict):
        for key in (_HEADER_ZONE_KEY, _USER_ZONE_KEY):
            if info.get(key) is not None:
                return info[key]
    return installation_zone()


# ─── Single values ────────────────────────────────────────────────────────────


def _format_utc(moment: datetime) -> str:
    m = moment.astimezone(UTC)
    return (
        f"{m.year:04d}-{m.month:02d}-{m.day:02d}"
        f"T{m.hour:02d}:{m.minute:02d}:{m.second:02d}Z"
    )


def _format_local(moment: datetime) -> str:
    return (
        f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"
        f"T{moment.hour:02d}:{moment.minute:02d}"
    )


def is_utc_time(value: Any) -> bool:
    """True when *value* is a time of day already stored in UTC."""
    return isinstance(value, str) and value.endswith("Z") and _TIMED_RE.match(value) is not None


def parse_time(value: Any, zone: ZoneInfo) -> Optional[datetime]:
    """
    *value* as an aware instant, or None when it carries no time of day.

    A value with ``Z`` or an offset is an instant already; one without is a
    local time in *zone*.
    """
    if not isinstance(value, str):
        return None
    m = _TIMED_RE.match(value.strip())
    if m is None:
        return None
    year, month, day, hour, minute = (int(m.group(i)) for i in range(1, 6))
    second = int(m.group(6) or 0)
    micro = int((m.group(7) or "0").ljust(6, "0"))
    suffix = m.group(8)
    try:
        if suffix == "Z":
            return datetime(year, month, day, hour, minute, second, micro, tzinfo=UTC)
        if suffix:
            sign = 1 if suffix[0] == "+" else -1
            offset = timedelta(hours=int(suffix[1:3]), minutes=int(suffix[4:6]))
            return datetime(year, month, day, hour, minute, second, micro,
                            tzinfo=timezone(sign * offset))
        return datetime(year, month, day, hour, minute, second, micro, tzinfo=zone, fold=0)
    except ValueError:
        return None


def time_to_utc(value: Any, zone: ZoneInfo) -> Any:
    """
    A time of day as stored: UTC with ``Z``. Anything else is returned
    unchanged, and so is a time that cannot be expressed in UTC within the
    years 1 to 9999.
    """
    moment = parse_time(value, zone)
    if moment is None:
        return value
    try:
        return _format_utc(moment)
    except (OverflowError, ValueError):
        return value


def time_to_local(value: Any, zone: ZoneInfo) -> Any:
    """
    A stored time of day as local ``YYYY-MM-DDTHH:MM`` in *zone*. Values
    without ``Z`` are local already and returned unchanged.
    """
    if not is_utc_time(value):
        return value
    moment = parse_time(value, zone)
    try:
        return _format_local(moment.astimezone(zone))
    except (OverflowError, ValueError, AttributeError):
        return value


def to_floating(value: Any, zone: ZoneInfo) -> Any:
    """
    An instant (``Z`` or offset) as local wall-clock time in *zone* with
    seconds, ``YYYY-MM-DDTHH:MM:SS`` (and microseconds when it has any);
    anything else unchanged. For values computed once for everybody
    (formulas, rollups).
    """
    if not isinstance(value, str):
        return value
    m = _TIMED_RE.match(value.strip())
    if m is None or not m.group(8):
        return value
    moment = parse_time(value, zone)
    try:
        local = moment.astimezone(zone)
    except (OverflowError, ValueError, AttributeError):
        return value
    text = f"{_format_local(local)}:{local.second:02d}"
    return f"{text}.{local.microsecond:06d}" if local.microsecond else text


# ─── Date property values ─────────────────────────────────────────────────────


_BOUNDARY_KEYS = ("start", "end")


def _map_bounds(value: dict, convert) -> dict:
    out = dict(value)
    for key in _BOUNDARY_KEYS:
        if key in out:
            out[key] = convert(out[key])
    timeline = out.get("_timeline")
    if isinstance(timeline, dict):
        slots = {}
        for period, slot in timeline.items():
            slots[period] = _map_bounds(slot, convert) if isinstance(slot, dict) else slot
        out["_timeline"] = slots
    return out


def date_value_to_utc(value: Any, zone: ZoneInfo) -> Any:
    """A ``date`` property value with every time of day in UTC."""
    if not isinstance(value, dict):
        return value
    return _map_bounds(value, lambda v: time_to_utc(v, zone))


def date_value_to_local(value: Any, zone: ZoneInfo) -> Any:
    """A stored ``date`` property value with every time of day in *zone*."""
    if not isinstance(value, dict):
        return value
    return _map_bounds(value, lambda v: time_to_local(v, zone))


def date_value_needs_utc(value: Any) -> bool:
    """True when a ``date`` value still holds a local time of day."""
    if not isinstance(value, dict):
        return False
    found = []

    def check(v: Any) -> Any:
        if parse_time(v, UTC) is not None and not is_utc_time(v):
            found.append(v)
        return v

    _map_bounds(value, check)
    return bool(found)
