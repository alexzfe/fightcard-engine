"""Minimal data/ reader/writer for this stage until `fightcard.store` lands.

Follows docs/CONTRACT.md formatting: UTF-8, 2-space indent, sorted keys, trailing newline.
"""

from __future__ import annotations

import json
from pathlib import Path

from fightcard.models import Event, Fighter


class DataError(Exception):
    """data/ is missing or unreadable."""


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DataError(f"cannot read {path}: {e}") from e


def load_fighters(data_dir: Path) -> dict[str, Fighter]:
    raw = _read(data_dir / "fighters.json")
    try:
        return {fid: Fighter.model_validate(f) for fid, f in raw.items()}
    except (AttributeError, ValueError) as e:
        raise DataError(f"invalid fighters.json: {e}") from e


def save_fighters(data_dir: Path, fighters: dict[str, Fighter]) -> None:
    out = {fid: f.model_dump(mode="json") for fid, f in fighters.items()}
    text = json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    (data_dir / "fighters.json").write_text(text, encoding="utf-8")


def load_events(data_dir: Path) -> list[Event]:
    events_dir = data_dir / "events"
    if not events_dir.is_dir():
        return []
    try:
        return [Event.model_validate(_read(p)) for p in sorted(events_dir.glob("*.json"))]
    except ValueError as e:
        raise DataError(f"invalid event file: {e}") from e
