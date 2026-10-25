import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    (d / "events").mkdir(parents=True)
    return d


@pytest.fixture
def make_event(data_dir: Path):
    def _make(event_id: str, date: str, name: str | None = None, status: str = "upcoming"):
        write_json(
            data_dir / "events" / f"{event_id}.json",
            {
                "id": event_id,
                "slug": event_id,
                "name": name or f"Event {event_id}",
                "date": date,
                "status": status,
                "fights": [],
            },
        )

    return _make


@pytest.fixture
def make_index(data_dir: Path):
    def _make(upcoming=(), recent=(), model=None):
        write_json(
            data_dir / "index.json",
            {
                "generated_at": "2026-10-24T06:00:00Z",
                "upcoming": list(upcoming),
                "recent": list(recent),
                "model": model,
            },
        )

    return _make
