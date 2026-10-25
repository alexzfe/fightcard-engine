from pathlib import Path

import pytest

from ops_helpers import write_json


# Registered here as well as in scripts/pytest.ini: a combined `pytest pipeline/tests scripts/tests` run uses
# pipeline's config (strict markers) instead. Docker tests are slow, so they only run when `-m` names them.
def pytest_configure(config):
    config.addinivalue_line("markers", "docker: builds and runs the site image; run explicitly with -m docker")


def pytest_collection_modifyitems(config, items):
    if "docker" in (config.option.markexpr or ""):
        return
    skip = pytest.mark.skip(reason="docker test; select with -m docker")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)


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
