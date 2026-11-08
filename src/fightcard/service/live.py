"""Which events the fight-night fast loop follows (docs/SERVICE.md, "Live refresh").

An event is *live* while it is indexed (index.json's upcoming or recent), dated today or yesterday in UTC (US cards end
after midnight UTC), and not fully resulted: some bout has no result with a W/L outcome yet. `fightcard scrape --live`
refetches exactly these, and the supervisor starts a live run only while there is one.

Stdlib only, reading data/'s JSON directly: the supervisor (PID 1) calls it every few minutes, so it stays small.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

UNCHANGED = 3  # `fightcard scrape --live`: nothing new, data/ untouched; the runner skips score and publish
LIVE_DAYS = 1  # today and this many days back; inside the scrape's RESULTS_GRACE (a test checks it)


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: {e}") from e


def resulted(fight: dict) -> bool:
    result = fight.get("result")
    return bool(result and result.get("outcome"))


def is_live(event: dict, today: date) -> bool:
    window = {(today - timedelta(days=n)).isoformat() for n in range(LIVE_DAYS + 1)}
    fights = event.get("fights") or []
    return event.get("date") in window and bool(fights) and not all(resulted(f) for f in fights)


def due_events(data_dir: Path, today: date) -> list[str]:
    """The live events' ids, in index order. Empty without an index.json; ValueError if it or an event is unreadable."""
    data_dir = Path(data_dir)
    index_path = data_dir / "index.json"
    if not index_path.exists():
        return []
    index = _read(index_path)
    due = []
    for event_id in dict.fromkeys([*index.get("upcoming", []), *index.get("recent", [])]):
        path = data_dir / "events" / f"{event_id}.json"
        if path.exists() and is_live(_read(path), today):
            due.append(event_id)
    return due
