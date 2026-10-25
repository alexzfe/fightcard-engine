"""Read and write data/ per docs/CONTRACT.md.

All JSON is UTF-8, 2-space indent, keys sorted, trailing newline. Writes are
atomic (temp file + rename) so a crashed run never leaves half a file.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from fightcard.models import Event, Fighter, Index


def dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dumps(data))
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.events_dir = self.root / "events"
        self.fighters_path = self.root / "fighters.json"
        self.index_path = self.root / "index.json"

    # events/<id>.json
    def load_event(self, event_id: str) -> Event | None:
        data = read_json(self.events_dir / f"{event_id}.json")
        return Event.model_validate(data) if data is not None else None

    def load_events(self) -> dict[str, Event]:
        if not self.events_dir.is_dir():
            return {}
        events = (Event.model_validate(read_json(p)) for p in sorted(self.events_dir.glob("*.json")))
        return {e.id: e for e in events}

    def save_event(self, event: Event) -> None:
        write_json(self.events_dir / f"{event.id}.json", event.model_dump(mode="json"))

    # fighters.json
    def load_fighters(self) -> dict[str, Fighter]:
        data = read_json(self.fighters_path, default={})
        return {fid: Fighter.model_validate(f) for fid, f in data.items()}

    def save_fighters(self, fighters: dict[str, Fighter]) -> None:
        write_json(self.fighters_path, {fid: f.model_dump(mode="json") for fid, f in fighters.items()})

    # index.json
    def load_index(self) -> Index | None:
        data = read_json(self.index_path)
        return Index.model_validate(data) if data is not None else None

    def save_index(self, index: Index) -> None:
        write_json(self.index_path, index.model_dump(mode="json"))
