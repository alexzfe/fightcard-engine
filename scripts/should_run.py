"""Decide whether this refresh.yml run should do any work.

The workflow fires daily at 06:00 UTC and once an hour. The daily run and manual runs always go ahead; an
hourly run only goes ahead on an event's day or the day after (US cards end after midnight UTC, and results
are scraped the day after).

Stdlib only, so the gate job doesn't have to install the pipeline.

    python3 scripts/should_run.py --data data --event-name schedule --schedule "17 * * * *"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from gh_output import emit

# Must match the daily `cron:` entry in .github/workflows/refresh.yml (a test checks it).
DAILY_CRON = "0 6 * * *"


def utc_today() -> date:
    return datetime.now(UTC).date()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: {e}") from e


def decide(data_dir: Path, *, event_name: str, schedule: str, today: date) -> tuple[bool, str]:
    if event_name != "schedule":
        return True, f"{event_name} run"
    if schedule == DAILY_CRON:
        return True, "daily run"

    index_path = data_dir / "index.json"
    if not index_path.exists():
        return True, "no index.json yet"
    index = _read_json(index_path)

    window = {today.isoformat(), (today - timedelta(days=1)).isoformat()}
    for event_id in [*index.get("upcoming", []), *index.get("recent", [])]:
        event_path = data_dir / "events" / f"{event_id}.json"
        if event_path.exists() and _read_json(event_path).get("date") in window:
            return True, f"event {event_id} is today or yesterday"
    return False, "no event today or yesterday"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--event-name", required=True, help="github.event_name")
    parser.add_argument("--schedule", default="", help="github.event.schedule (the cron that fired)")
    parser.add_argument("--today", type=date.fromisoformat, help="YYYY-MM-DD; default today in UTC")
    args = parser.parse_args(argv)

    run, reason = decide(
        args.data, event_name=args.event_name, schedule=args.schedule, today=args.today or utc_today()
    )
    print(reason)
    emit(run=run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
