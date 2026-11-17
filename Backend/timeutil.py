"""Time helpers.

Cloud hosts (Render, etc.) run in UTC. Attendance rules such as "late after 09:30" and "today" must follow the
organisation's own timezone, so every place that needs "now" or "today" uses these helpers instead of
datetime.now() / date.today().  Timestamps stored for auditing (created_at ...) are UTC.
"""
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Kolkata")
try:
    _TZ = ZoneInfo(APP_TIMEZONE)
except Exception:  # unknown tz name or missing tzdata -> fall back to UTC rather than crash
    _TZ = timezone.utc


def now_local() -> datetime:
    """Current wall-clock time in the organisation's timezone (naive datetime)."""
    return datetime.now(_TZ).replace(tzinfo=None)


def today_local() -> date:
    return now_local().date()


def utcnow() -> datetime:
    """Naive UTC datetime (replacement for the deprecated datetime.utcnow)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_utc(dt: datetime | None) -> str | None:
    """Serialise a stored UTC datetime so browsers parse it as UTC (trailing Z) and show local time."""
    return dt.isoformat() + "Z" if dt else None
