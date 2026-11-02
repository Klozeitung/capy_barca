"""
Tests for app.timezones: reading and showing times of day across zones and
daylight saving time. Pure functions, no database.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app import timezones as tz

BERLIN = ZoneInfo("Europe/Berlin")


def test_winter_and_summer_times_get_their_own_offset():
    assert tz.time_to_utc("2045-01-17T09:00", BERLIN) == "2045-01-17T08:00:00Z"
    assert tz.time_to_utc("2045-08-17T09:00", BERLIN) == "2045-08-17T07:00:00Z"


def test_a_summer_time_entered_in_winter_still_shows_as_entered():
    stored = tz.time_to_utc("2045-08-17T09:00", BERLIN)
    assert tz.time_to_local(stored, BERLIN) == "2045-08-17T09:00"


def test_the_skipped_spring_hour_moves_forward():
    stored = tz.time_to_utc("2045-03-26T02:30", BERLIN)
    assert tz.time_to_local(stored, BERLIN) == "2045-03-26T03:30"


def test_the_doubled_autumn_hour_is_its_first_occurrence():
    assert tz.time_to_utc("2045-10-29T02:30", BERLIN) == "2045-10-29T00:30:00Z"


def test_dates_without_time_and_other_values_stay_as_they_are():
    for value in ("2045-07-17", "", None, 5, "not a date"):
        assert tz.time_to_utc(value, BERLIN) == value
        assert tz.time_to_local(value, BERLIN) == value


def test_values_with_an_offset_or_z_are_instants_already():
    assert tz.time_to_utc("2045-07-17T09:00:00+02:00", BERLIN) == "2045-07-17T07:00:00Z"
    assert tz.time_to_utc("2045-07-17T07:00:00Z", BERLIN) == "2045-07-17T07:00:00Z"


def test_local_values_written_before_the_conversion_are_shown_unchanged():
    assert tz.time_to_local("2045-07-17T09:00", BERLIN) == "2045-07-17T09:00"


@pytest.mark.parametrize("local", ["0005-03-01T09:00", "1700-06-01T12:15", "9000-12-31T23:59"])
def test_far_years_round_trip_exactly(local):
    assert tz.time_to_local(tz.time_to_utc(local, BERLIN), BERLIN) == local


def test_a_time_that_would_leave_the_calendar_is_kept_as_entered():
    assert tz.time_to_utc("0001-01-01T00:10", BERLIN) == "0001-01-01T00:10"


def test_date_values_convert_start_end_and_timeline_slots_but_not_keys():
    value = {
        "start": "2045-07-17T09:00",
        "end": "2045-07-17T10:00",
        "repeat": "weekly",
        "_timeline": {"2045-01-01→": {"start": "2045-01-02T09:00", "end": None}},
    }
    stored = tz.date_value_to_utc(value, BERLIN)
    assert stored["start"] == "2045-07-17T07:00:00Z"
    assert stored["end"] == "2045-07-17T08:00:00Z"
    assert stored["repeat"] == "weekly"
    assert list(stored["_timeline"]) == ["2045-01-01→"]
    assert stored["_timeline"]["2045-01-01→"]["start"] == "2045-01-02T08:00:00Z"
    assert tz.date_value_to_local(stored, BERLIN) == value


def test_needs_utc_spots_local_times_only():
    assert tz.date_value_needs_utc({"start": "2045-07-17T09:00"})
    assert not tz.date_value_needs_utc({"start": "2045-07-17T07:00:00Z"})
    assert not tz.date_value_needs_utc({"start": "2045-07-17"})
    assert not tz.date_value_needs_utc(None)


def test_floating_keeps_seconds_and_fractions():
    assert tz.to_floating("2045-07-17T07:00:00Z", BERLIN) == "2045-07-17T09:00:00"
    assert tz.to_floating("2045-07-17T07:00:00.250000+00:00", BERLIN) == "2045-07-17T09:00:00.250000"
    assert tz.to_floating("2045-07-17T09:00", BERLIN) == "2045-07-17T09:00"


def test_zones_fall_back_from_header_to_account_to_installation(monkeypatch):
    class Session:
        def __init__(self):
            self.info = {}

    class Account:
        timezone = "America/New_York"

    monkeypatch.setenv(tz.INSTALLATION_ZONE_ENV, "Asia/Tokyo")
    session = Session()
    assert str(tz.zone_of(session)) == "Asia/Tokyo"
    tz.set_user_zone(session, Account())
    assert str(tz.zone_of(session)) == "America/New_York"
    tz.set_header_zone(session, "Nowhere/Land")
    assert str(tz.zone_of(session)) == "America/New_York"
    tz.set_header_zone(session, "Europe/Berlin")
    assert str(tz.zone_of(session)) == "Europe/Berlin"


def test_unknown_installation_zone_means_utc(monkeypatch):
    monkeypatch.setenv(tz.INSTALLATION_ZONE_ENV, "Nowhere/Land")
    assert str(tz.installation_zone()) == "UTC"


def test_parse_time_reads_local_times_in_the_zone():
    moment = tz.parse_time("2045-08-17T09:00", BERLIN)
    assert moment.astimezone(timezone.utc) == datetime(2045, 8, 17, 7, 0, tzinfo=timezone.utc)
