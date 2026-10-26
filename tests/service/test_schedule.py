"""The scheduler's slots: hourly at :17 and daily at 06:00 UTC, the crons scripts/should_run.py knows."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fightcard.service.schedule import DAILY_CRON, HOURLY_CRON, next_fire

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import should_run  # noqa: E402


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_daily_cron_is_the_one_the_gate_always_runs():
    assert DAILY_CRON == should_run.DAILY_CRON
    assert HOURLY_CRON == "17 * * * *"


@pytest.mark.parametrize("now,when,cron", [
    ("2026-10-26 05:20:00", "2026-10-26 06:00:00", DAILY_CRON),
    ("2026-10-26 05:59:59", "2026-10-26 06:00:00", DAILY_CRON),
    ("2026-10-26 06:00:00", "2026-10-26 06:17:00", HOURLY_CRON),  # strictly after now
    ("2026-10-26 06:16:30", "2026-10-26 06:17:00", HOURLY_CRON),
    ("2026-10-26 06:17:00", "2026-10-26 07:17:00", HOURLY_CRON),
    ("2026-10-26 23:30:00", "2026-10-27 00:17:00", HOURLY_CRON),
    ("2026-10-26 05:10:00", "2026-10-26 05:17:00", HOURLY_CRON),
    ("2026-12-31 23:59:00", "2027-01-01 00:17:00", HOURLY_CRON),
])
def test_next_fire(now, when, cron):
    assert next_fire(at(now)) == (at(when), cron)


def test_next_fire_works_in_utc_whatever_the_input_zone():
    from datetime import timedelta, timezone
    local = datetime(2026, 10, 26, 7, 30, tzinfo=timezone(timedelta(hours=2)))  # 05:30 UTC
    assert next_fire(local) == (at("2026-10-26 06:00:00"), DAILY_CRON)


def test_one_day_has_24_hourly_slots_and_one_daily():
    now, seen = at("2026-10-26 00:00:00"), []
    while True:
        now, cron = next_fire(now)
        if now >= at("2026-10-27 00:00:00"):
            break
        seen.append(cron)
    assert seen.count(HOURLY_CRON) == 24 and seen.count(DAILY_CRON) == 1
