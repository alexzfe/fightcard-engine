"""The two slots of the refresh schedule, in UTC: every hour at :17, and daily at 06:00.

The cron strings are what scripts/should_run.py is told fired; it always lets DAILY_CRON through. Another `minute`
(staging's :47, see Config.schedule_minute) moves both slots by the same amount, so the daily one fires at 06:30 but
still reports DAILY_CRON."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

MINUTE = 17
HOURLY_CRON = f"{MINUTE} * * * *"
DAILY_CRON = "0 6 * * *"  # must match scripts/should_run.py (a test checks it)


def next_fire(now: datetime, minute: int = MINUTE) -> tuple[datetime, str]:
    """The first slot strictly after `now`, and its cron."""
    now = now.astimezone(UTC)
    hourly = now.replace(minute=minute, second=0, microsecond=0)
    if hourly <= now:
        hourly += timedelta(hours=1)
    daily = now.replace(hour=6, minute=(minute - MINUTE) % 60, second=0, microsecond=0)
    if daily <= now:
        daily += timedelta(days=1)
    return (daily, DAILY_CRON) if daily < hourly else (hourly, f"{minute} * * * *")
