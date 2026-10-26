"""The two slots of the refresh schedule, in UTC: every hour at :17, and daily at 06:00.

The cron strings are what scripts/should_run.py is told fired; it always lets DAILY_CRON through."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

HOURLY_CRON = "17 * * * *"
DAILY_CRON = "0 6 * * *"  # must match scripts/should_run.py (a test checks it)


def next_fire(now: datetime) -> tuple[datetime, str]:
    """The first slot strictly after `now`, and its cron."""
    now = now.astimezone(UTC)
    hourly = now.replace(minute=17, second=0, microsecond=0)
    if hourly <= now:
        hourly += timedelta(hours=1)
    daily = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if daily <= now:
        daily += timedelta(days=1)
    return (daily, DAILY_CRON) if daily < hourly else (hourly, HOURLY_CRON)
