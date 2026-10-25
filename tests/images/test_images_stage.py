import argparse
import json
from datetime import datetime, timezone

import pytest
from images_helpers import png_bytes

from fightcard.images import stage
from fightcard.images.http import HttpClient, Response
from fightcard.images.sources import (
    ESPN_SCOREBOARD_URL,
    ESPN_SEARCH_URL,
    WIKIPEDIA_API_URL,
    headshot_url,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)

FIGHTERS = {
    "f1": {"id": "f1", "name": "Alexander Volkanovski"},
    "f2": {"id": "f2", "name": "Movsar Evloev"},
    "f3": {"id": "f3", "name": "Petr Yan", "image": "images/f3.png"},
    "f4": {"id": "f4", "name": "Nobody Atall"},
    "old1": {"id": "old1", "name": "Retired Guy"},
    "old2": {"id": "old2", "name": "Other Retired"},
}

BOARD = {"events": [{"competitions": [{"competitors": [
    {"id": "3949584", "athlete": {"displayName": "Alexander Volkanovski"}},
    {"id": "4029275", "athlete": {"displayName": "Movsar Evloev"}},
]}]}]}


def _event(eid, date, status, pairs):
    return {
        "id": eid, "slug": eid, "name": eid, "date": date, "status": status,
        "fights": [{"id": f"{eid}-{i}", "red": r, "blue": b} for i, (r, b) in enumerate(pairs)],
    }


@pytest.fixture
def data(tmp_path):
    (tmp_path / "events").mkdir()
    (tmp_path / "fighters.json").write_text(json.dumps(FIGHTERS))
    for ev in (
        _event("e2", "2026-11-01", "upcoming", [("f3", "f4")]),
        _event("e1", "2026-10-25", "upcoming", [("f1", "f2")]),
        _event("e0", "2026-10-01", "completed", [("old1", "old2")]),
    ):
        (tmp_path / "events" / f"{ev['id']}.json").write_text(json.dumps(ev))
    return tmp_path


@pytest.fixture
def client(transport, clock):
    return HttpClient(transport, min_interval=1.0, clock=clock.time, sleep=clock.sleep)


def _ok_routes(transport):
    transport.add_json(ESPN_SCOREBOARD_URL, BOARD)
    transport.add(headshot_url("3949584"), Response(200, png_bytes()))
    transport.add(headshot_url("4029275"), Response(200, png_bytes(fmt="JPEG")))
    transport.add_json(ESPN_SEARCH_URL, {"items": []})
    transport.add_json(WIKIPEDIA_API_URL, {"query": {"pages": {"-1": {"missing": ""}}}})


def _fighters(data):
    return json.loads((data / "fighters.json").read_text())


def test_downloads_headshots_for_upcoming_fighters_without_image(data, client, transport):
    _ok_routes(transport)
    summary = stage.process(data, client, NOW)
    assert (data / "images" / "f1.png").read_bytes() == png_bytes()
    assert (data / "images" / "f2.jpg").exists()
    fighters = _fighters(data)
    assert fighters["f1"]["image"] == "images/f1.png"
    assert fighters["f2"]["image"] == "images/f2.jpg"
    assert summary.hits == 2 and summary.misses == 1


def test_skips_fighters_with_images_and_past_events(data, client, transport):
    _ok_routes(transport)
    stage.process(data, client, NOW)
    queried = " ".join(" ".join(str(v) for v in p.values()) for _, p in transport.calls)
    assert "Petr Yan" not in queried
    assert "Retired Guy" not in queried


def test_miss_is_recorded_and_not_retried_within_a_week(data, client, transport):
    _ok_routes(transport)
    stage.process(data, client, NOW)
    misses = json.loads((data / "images" / "misses.json").read_text())
    assert misses == {"f4": "2026-10-24T12:00:00Z"}
    assert _fighters(data)["f4"]["image"] is None

    transport.calls.clear()
    summary = stage.process(data, client, NOW)
    assert transport.calls == []
    assert summary.skipped == 1


def test_old_miss_is_retried_and_cleared_on_hit(data, client, transport):
    (data / "images").mkdir()
    (data / "images" / "misses.json").write_text('{"f1": "2026-10-01T00:00:00Z"}')
    _ok_routes(transport)
    stage.process(data, client, NOW)
    misses = json.loads((data / "images" / "misses.json").read_text())
    assert "f1" not in misses
    assert _fighters(data)["f1"]["image"] == "images/f1.png"


def test_placeholder_image_falls_through_to_next_candidate(data, client, transport):
    transport.add_json(ESPN_SCOREBOARD_URL, BOARD)
    transport.add(headshot_url("3949584"), Response(200, png_bytes(1, 1)))
    transport.add_json(
        WIKIPEDIA_API_URL,
        {"query": {"pages": {"5": {"title": "Alexander Volkanovski",
                                    "thumbnail": {"source": "https://upload.wikimedia.org/v.jpg"}}}}},
        query="Alexander Volkanovski",
    )
    transport.add("https://upload.wikimedia.org/v.jpg", Response(200, png_bytes(fmt="JPEG")))
    stage.process(data, client, NOW)
    assert _fighters(data)["f1"]["image"] == "images/f1.jpg"


def test_limit_caps_lookups(data, client, transport):
    _ok_routes(transport)
    summary = stage.process(data, client, NOW, limit=1)
    assert summary.hits + summary.misses == 1
    assert _fighters(data)["f1"]["image"] == "images/f1.png"  # soonest event first
    assert _fighters(data)["f2"]["image"] is None


def test_network_down_is_not_fatal(data, client, transport):
    transport.add("https://", OSError("offline"))
    summary = stage.process(data, client, NOW)
    assert summary.hits == 0 and summary.misses == 3


def test_fighter_missing_from_fighters_json_is_ignored(data, client, transport):
    ev = _event("e3", "2026-10-26", "upcoming", [("ghost", "f1")])
    (data / "events" / "e3.json").write_text(json.dumps(ev))
    _ok_routes(transport)
    stage.process(data, client, NOW)
    assert "ghost" not in _fighters(data)


def test_fighters_json_is_written_per_contract(data, client, transport):
    _ok_routes(transport)
    stage.process(data, client, NOW)
    text = (data / "fighters.json").read_text()
    assert text.endswith("}\n") and text.startswith('{\n  "f1": {')
    assert json.loads(text) == json.loads(json.dumps(json.loads(text), sort_keys=True))
    assert list(json.loads(text)) == sorted(json.loads(text))


def test_no_events_dir_is_a_noop(tmp_path, client):
    (tmp_path / "fighters.json").write_text("{}")
    assert stage.process(tmp_path, client, NOW).hits == 0


def _args(data, limit=None):
    return argparse.Namespace(data=data, limit=limit)


def test_run_exits_zero_and_uses_client_factory(data, client, transport, monkeypatch):
    _ok_routes(transport)
    monkeypatch.setattr(stage, "make_client", lambda: client)
    assert stage.run(_args(data, limit=1)) == 0
    assert _fighters(data)["f1"]["image"] == "images/f1.png"


def test_run_exits_nonzero_when_data_unreadable(tmp_path, client, monkeypatch):
    monkeypatch.setattr(stage, "make_client", lambda: client)
    assert stage.run(_args(tmp_path / "nope")) == 1
    (tmp_path / "fighters.json").write_text("{broken")
    assert stage.run(_args(tmp_path)) == 1


def test_cli_wires_images_stage_with_limit(data, client, transport, monkeypatch):
    from fightcard.cli import main

    _ok_routes(transport)
    monkeypatch.setattr(stage, "make_client", lambda: client)
    assert main(["--data", str(data), "images", "--limit", "2"]) == 0
    assert _fighters(data)["f2"]["image"] == "images/f2.jpg"


def test_make_client_returns_rate_limited_client():
    c = stage.make_client()
    assert isinstance(c, HttpClient) and c.min_interval >= 1.0


def test_run_exits_nonzero_on_malformed_event_or_fighters(data, client, monkeypatch):
    monkeypatch.setattr(stage, "make_client", lambda: client)
    (data / "events" / "bad.json").write_text('{"id": "bad"}')
    assert stage.run(_args(data)) == 1
    (data / "events" / "bad.json").unlink()
    (data / "fighters.json").write_text("[1, 2]")
    assert stage.run(_args(data)) == 1


def test_fighter_with_existing_image_keeps_it(data, client, transport):
    _ok_routes(transport)
    stage.process(data, client, NOW)
    assert _fighters(data)["f3"]["image"] == "images/f3.png"


def test_no_writes_when_nothing_to_do(tmp_path, client):
    (tmp_path / "events").mkdir()
    (tmp_path / "fighters.json").write_text("{}")
    stage.process(tmp_path, client, NOW)
    assert not (tmp_path / "images").exists()
    assert (tmp_path / "fighters.json").read_text() == "{}"
