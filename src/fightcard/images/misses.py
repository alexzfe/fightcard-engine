"""data/images/misses.json: {fighter_id: ISO last tried}; retry after RETRY_AFTER."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

RETRY_AFTER = timedelta(days=7)


def _iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


class Misses:
    def __init__(self, path: Path, entries: dict[str, str]) -> None:
        self.path = path
        self.entries = entries

    @classmethod
    def load(cls, path: Path) -> Misses:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        return cls(path, data if isinstance(data, dict) else {})

    def recently_tried(self, fighter_id: str, now: datetime) -> bool:
        stamp = self.entries.get(fighter_id)
        if not stamp:
            return False
        try:
            tried = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return False
        return now - tried < RETRY_AFTER

    def record(self, fighter_id: str, now: datetime) -> None:
        self.entries[fighter_id] = _iso(now)

    def clear(self, fighter_id: str) -> None:
        self.entries.pop(fighter_id, None)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=2, sort_keys=True) + "\n")
