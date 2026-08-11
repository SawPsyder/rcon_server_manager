"""App-timezone helpers and next-run math for server schedules.

All schedule ``time_local`` values are interpreted in the single manager
timezone stored as ``settings.app_timezone`` (default UTC). Schedules never
carry their own zone.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.models import Setting

logger = logging.getLogger(__name__)

DEFAULT_APP_TIMEZONE = "UTC"
TIME_LOCAL_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# Monday=0 … Sunday=6, matching datetime.weekday().
ALL_WEEKDAYS = frozenset(range(7))


def parse_time_local(value: str) -> tuple[int, int]:
    """Return (hour, minute) or raise ValueError."""
    text = (value or "").strip()
    m = TIME_LOCAL_RE.match(text)
    if not m:
        raise ValueError(f"Invalid time_local {value!r}; expected HH:MM (24h)")
    return int(m.group(1)), int(m.group(2))


def parse_days_of_week(value: str | None) -> set[int]:
    """Parse ``\"0,1,2,3,4,5,6\"`` (Mon=0 … Sun=6). Empty → every day."""
    text = (value or "").strip()
    if not text:
        return set(ALL_WEEKDAYS)
    days: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        day = int(part)
        if day < 0 or day > 6:
            raise ValueError(f"Weekday out of range: {day}")
        days.add(day)
    if not days:
        return set(ALL_WEEKDAYS)
    return days


def format_days_of_week(days: set[int] | list[int]) -> str:
    return ",".join(str(d) for d in sorted(set(days)))


def resolve_zone(name: str | None) -> ZoneInfo:
    """Resolve an IANA zone name; fall back to UTC on missing/invalid."""
    key = (name or "").strip() or DEFAULT_APP_TIMEZONE
    try:
        return ZoneInfo(key)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone %r; falling back to UTC", key)
        return ZoneInfo(DEFAULT_APP_TIMEZONE)


def validate_timezone_name(name: str) -> str:
    """Return the stripped name or raise ValueError if zoneinfo rejects it."""
    key = (name or "").strip()
    if not key:
        raise ValueError("Timezone must not be empty")
    try:
        ZoneInfo(key)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {key}") from exc
    return key


def load_app_timezone(db: Session) -> str:
    """IANA name from settings; never raises (invalid → UTC)."""
    row = db.query(Setting).filter(Setting.key == "app_timezone").first()
    raw = (row.value if row else "") or DEFAULT_APP_TIMEZONE
    try:
        return validate_timezone_name(raw)
    except ValueError:
        logger.warning("Stored app_timezone %r is invalid; using UTC", raw)
        return DEFAULT_APP_TIMEZONE


def load_app_zoneinfo(db: Session) -> ZoneInfo:
    return resolve_zone(load_app_timezone(db))


def _aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def next_occurrence(
    *,
    time_local: str,
    days_of_week: str | set[int] | None,
    tz: ZoneInfo | str,
    after: datetime | None = None,
    inclusive: bool = False,
) -> datetime:
    """Next UTC instant for ``time_local`` on an allowed weekday.

    ``after`` defaults to now (UTC). When ``inclusive`` is True, a matching
    instant equal to ``after`` is accepted; otherwise the next later one is
    returned (used after a successful run so the same minute does not re-fire).
    """
    hour, minute = parse_time_local(time_local)
    days = (
        days_of_week
        if isinstance(days_of_week, set)
        else parse_days_of_week(days_of_week if isinstance(days_of_week, str) else None)
    )
    zone = resolve_zone(tz) if isinstance(tz, str) else tz
    after_utc = _aware_utc(after or datetime.now(timezone.utc))
    local_after = after_utc.astimezone(zone)

    # Search up to 8 days so a restricted weekday set still resolves.
    # Note: on DST spring-forward, a local wall time in the gap (e.g. 02:30
    # Europe/Berlin) may still construct under zoneinfo and map to an offset
    # that never appears on clocks that day. Prefer 03:00+ for maintenance
    # windows in zones with DST.
    for day_offset in range(0, 8):
        candidate_date = (local_after + timedelta(days=day_offset)).date()
        if candidate_date.weekday() not in days:
            continue
        local_candidate = datetime(
            candidate_date.year,
            candidate_date.month,
            candidate_date.day,
            hour,
            minute,
            tzinfo=zone,
        )
        if inclusive:
            if local_candidate >= local_after:
                return local_candidate.astimezone(timezone.utc)
        else:
            if local_candidate > local_after:
                return local_candidate.astimezone(timezone.utc)

    # Should be unreachable with any non-empty weekday set.
    raise RuntimeError("Could not compute next schedule occurrence")


def window_after(
    *,
    scheduled_for: datetime,
    time_local: str,
    days_of_week: str | set[int] | None,
    tz: ZoneInfo | str,
) -> datetime:
    """The next calendar window after ``scheduled_for`` (exclusive)."""
    return next_occurrence(
        time_local=time_local,
        days_of_week=days_of_week,
        tz=tz,
        after=_aware_utc(scheduled_for),
        inclusive=False,
    )


def compute_retry_next_run(
    *,
    now: datetime,
    retry_after_minutes: int,
    scheduled_for: datetime,
    time_local: str,
    days_of_week: str | set[int] | None,
    tz: ZoneInfo | str,
) -> tuple[datetime | None, datetime]:
    """Return ``(next_retry_or_None_if_skip, next_window)``.

    When a retry would land at or after the next window, the caller should
    skip the current window and schedule for ``next_window`` instead.
    """
    now_utc = _aware_utc(now)
    next_window = window_after(
        scheduled_for=scheduled_for,
        time_local=time_local,
        days_of_week=days_of_week,
        tz=tz,
    )
    # 0 (or negative) means do not retry — skip to the next calendar window.
    if int(retry_after_minutes) <= 0:
        return None, next_window
    delay = max(1, int(retry_after_minutes))
    retry_at = now_utc + timedelta(minutes=delay)
    if retry_at >= next_window:
        return None, next_window
    return retry_at, next_window
