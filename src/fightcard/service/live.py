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


def _ids(index, path: Path) -> list[str]:
    if not isinstance(index, dict):
        raise ValueError(f"{path}: not a JSON object")
    lists = [index.get(key, []) for key in ("upcoming", "recent")]
    if not all(isinstance(ids, list) and all(isinstance(i, str) for i in ids) for ids in lists):
        raise ValueError(f"{path}: upcoming and recent must be lists of event ids")
    return list(dict.fromkeys(lists[0] + lists[1]))


def _is_live(path: Path, today: date) -> bool:
    try:
        return is_live(_read(path), today)
    except (AttributeError, TypeError) as e:  # valid JSON, wrong shape (null, a list, a null fight...)
        raise ValueError(f"{path}: not an event: {e}") from e


def due_events(data_dir: Path, today: date) -> list[str]:
    """The live events' ids, in index order. Empty without an index.json; ValueError if it or an event is unreadable
    or the wrong shape, so callers (serve, PID 1, among them) need catch only OSError and ValueError."""
    data_dir = Path(data_dir)
    index_path = data_dir / "index.json"
    if not index_path.exists():
        return []
    due = []
    for event_id in _ids(_read(index_path), index_path):
        path = data_dir / "events" / f"{event_id}.json"
        if path.exists() and _is_live(path, today):
            due.append(event_id)
    return due
