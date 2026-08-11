"""Unit tests for schedule next-run and retry math."""

from datetime import datetime, timezone

from zoneinfo import ZoneInfo

from app.services.schedule_time import (
    compute_retry_next_run,
    format_days_of_week,
    next_occurrence,
    parse_days_of_week,
    parse_time_local,
    validate_timezone_name,
    window_after,
)


def test_parse_time_local():
    assert parse_time_local("04:00") == (4, 0)
    assert parse_time_local("23:59") == (23, 59)


def test_parse_days_default_all():
    assert parse_days_of_week("") == set(range(7))
    assert parse_days_of_week("0,2,4") == {0, 2, 4}
    assert format_days_of_week({4, 0, 2}) == "0,2,4"


def test_validate_timezone():
    assert validate_timezone_name("Europe/Berlin") == "Europe/Berlin"
    assert validate_timezone_name(" UTC ") == "UTC"


def test_next_occurrence_same_day_later():
    # 2026-08-11 is a Tuesday (weekday 1). Noon UTC = 14:00 Berlin (CEST).
    after = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    nxt = next_occurrence(
        time_local="16:00",
        days_of_week="0,1,2,3,4,5,6",
        tz="Europe/Berlin",
        after=after,
        inclusive=False,
    )
    local = nxt.astimezone(ZoneInfo("Europe/Berlin"))
    assert local.hour == 16 and local.minute == 0
    assert local.date().isoformat() == "2026-08-11"


def test_next_occurrence_rolls_to_next_day():
    after = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    nxt = next_occurrence(
        time_local="04:00",
        days_of_week="0,1,2,3,4,5,6",
        tz="Europe/Berlin",
        after=after,
        inclusive=False,
    )
    local = nxt.astimezone(ZoneInfo("Europe/Berlin"))
    assert local.date().isoformat() == "2026-08-12"
    assert local.hour == 4


def test_next_occurrence_weekday_filter():
    # Only Mondays (0). 2026-08-11 is Tuesday → next Mon is 2026-08-17.
    after = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    nxt = next_occurrence(
        time_local="04:00",
        days_of_week="0",
        tz="UTC",
        after=after,
        inclusive=False,
    )
    assert nxt.weekday() == 0
    assert nxt.day == 17


def test_retry_until_next_window():
    scheduled = datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc)  # 04:00 Berlin
    now = scheduled + __import__("datetime").timedelta(minutes=5)
    retry, nxt = compute_retry_next_run(
        now=now,
        retry_after_minutes=10,
        scheduled_for=scheduled,
        time_local="04:00",
        days_of_week="0,1,2,3,4,5,6",
        tz="Europe/Berlin",
    )
    assert retry is not None
    assert retry > now
    assert nxt > scheduled


def test_retry_skipped_near_next_window():
    scheduled = datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc)
    # Almost a full day later, retry would pass next window.
    now = scheduled + __import__("datetime").timedelta(hours=23, minutes=55)
    retry, nxt = compute_retry_next_run(
        now=now,
        retry_after_minutes=10,
        scheduled_for=scheduled,
        time_local="04:00",
        days_of_week="0,1,2,3,4,5,6",
        tz="Europe/Berlin",
    )
    assert retry is None
    assert nxt == window_after(
        scheduled_for=scheduled,
        time_local="04:00",
        days_of_week="0,1,2,3,4,5,6",
        tz="Europe/Berlin",
    )


def test_retry_disabled_skips_window():
    scheduled = datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc)
    now = scheduled + __import__("datetime").timedelta(minutes=1)
    retry, nxt = compute_retry_next_run(
        now=now,
        retry_after_minutes=0,
        scheduled_for=scheduled,
        time_local="04:00",
        days_of_week="0,1,2,3,4,5,6",
        tz="Europe/Berlin",
    )
    assert retry is None
    assert nxt > scheduled
