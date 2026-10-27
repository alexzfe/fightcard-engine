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
    assert (data / "images" / "f1.webp").exists()
    assert (data / "images" / "f2.webp").exists()
    assert not (data / "images" / "f1.png").exists()
    fighters = _fighters(data)
    assert fighters["f1"]["image"] == "images/f1.webp"
    assert fighters["f2"]["image"] == "images/f2.webp"
    assert summary.hits == 2 and summary.misses == 1


def _queried(transport):
    return " ".join(" ".join(str(v) for v in p.values()) for _, p in transport.calls)


def _index(data, upcoming, recent):
    (data / "index.json").write_text(json.dumps(
        {"generated_at": "2026-10-24T00:00:00Z", "upcoming": upcoming, "recent": recent}
    ))


def test_without_index_skips_fighters_with_images_and_past_events(data, client, transport):
    _ok_routes(transport)
    stage.process(data, client, NOW)
    assert "Petr Yan" not in _queried(transport)
    assert "Retired Guy" not in _queried(transport)


def test_recent_results_on_the_site_get_headshots_but_the_archive_does_not(data, client, transport):
    # A card first scraped after it happened (UFC 333 on the first live run) was never upcoming.
    ev = _event("ea", "2026-08-01", "completed", [("arch1", "arch2")])
    (data / "events" / "ea.json").write_text(json.dumps(ev))
    archive = {"arch1": {"id": "arch1", "name": "Archive Only"}, "arch2": {"id": "arch2", "name": "Archive Too"}}
    (data / "fighters.json").write_text(json.dumps({**FIGHTERS, **archive}))
    _index(data, ["e1", "e2"], ["e0"])
    _ok_routes(transport)
    stage.process(data, client, NOW)
    assert "Retired Guy" in _queried(transport)
    assert "Other Retired" in _queried(transport)
    assert "Archive Only" not in _queried(transport)
    assert "Petr Yan" not in _queried(transport)


def test_upcoming_cards_come_before_recent_results(data, client, transport):
    _index(data, ["e1", "e2"], ["e0"])
    _ok_routes(transport)
    summary = stage.process(data, client, NOW, limit=3)
    assert summary.hits + summary.misses == 3
    assert "Nobody Atall" in _queried(transport)
    assert "Retired Guy" not in _queried(transport)


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
    assert _fighters(data)["f1"]["image"] == "images/f1.webp"


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
    assert _fighters(data)["f1"]["image"] == "images/f1.webp"


def test_limit_caps_lookups(data, client, transport):
    _ok_routes(transport)
    summary = stage.process(data, client, NOW, limit=1)
    assert summary.hits + summary.misses == 1
    assert _fighters(data)["f1"]["image"] == "images/f1.webp"  # soonest event first
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
    assert _fighters(data)["f1"]["image"] == "images/f1.webp"


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
    assert _fighters(data)["f2"]["image"] == "images/f2.webp"


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


def test_downloaded_headshot_is_stored_as_small_webp(data, client, transport):
    from PIL import Image

    _ok_routes(transport)
    transport.routes.insert(0, (headshot_url("3949584"), "", Response(200, png_bytes(600, 436))))
    stage.process(data, client, NOW)
    with Image.open(data / "images" / "f1.webp") as img:
        assert img.format == "WEBP" and img.size == (320, 233)


def test_existing_png_and_jpg_are_migrated_to_webp_without_network(data, client, transport):
    from PIL import Image

    images = data / "images"
    images.mkdir()
    (images / "f3.png").write_bytes(png_bytes(600, 436))
    (images / "old1.jpg").write_bytes(png_bytes(fmt="JPEG"))
    fighters = _fighters(data)
    fighters["old1"]["image"] = "images/old1.jpg"
    (data / "fighters.json").write_text(json.dumps(fighters))

    summary = stage.migrate_images(data)

    assert summary == 2
    assert not (images / "f3.png").exists() and not (images / "old1.jpg").exists()
    with Image.open(images / "f3.webp") as img:
        assert img.size == (320, 233)
    after = _fighters(data)
    assert after["f3"]["image"] == "images/f3.webp"
    assert after["old1"]["image"] == "images/old1.webp"
    assert transport.calls == []


def test_migration_is_idempotent_and_skips_misses_json(data):
    images = data / "images"
    images.mkdir()
    (images / "misses.json").write_text("{}\n")
    (images / "f3.png").write_bytes(png_bytes())
    assert stage.migrate_images(data) == 1
    before = (data / "fighters.json").read_text()
    assert stage.migrate_images(data) == 0
    assert (data / "fighters.json").read_text() == before
    assert (images / "misses.json").exists()


def test_migration_leaves_unreadable_image_alone(data):
    images = data / "images"
    images.mkdir()
    (images / "f3.png").write_bytes(b"<html>not an image</html>")
    assert stage.migrate_images(data) == 0
    assert (images / "f3.png").exists()
    assert _fighters(data)["f3"]["image"] == "images/f3.png"


def test_process_runs_migration_first(data, client, transport):
    images = data / "images"
    images.mkdir()
    (images / "f3.png").write_bytes(png_bytes())
    _ok_routes(transport)
    stage.process(data, client, NOW)
    assert _fighters(data)["f3"]["image"] == "images/f3.webp"


def test_migrate_does_not_rewrite_fighters_when_nothing_to_convert(data):
    before = (data / "fighters.json").read_text()
    assert stage.migrate_images(data) == 0
    assert (data / "fighters.json").read_text() == before


# --- P4P headshots: the ids in data/rankings.json (written by the previous `score`) ------------------------------------

def _rankings(data, men=(), women=()):
    def entries(rows):
        return [{"rank": i, "id": fid, "name": name, "elo": 1600, "division": None, "image": None}
                for i, (fid, name) in enumerate(rows, start=1)]
    (data / "rankings.json").write_text(json.dumps(
        {"as_of": "2026-10-23", "p4p": {"men": entries(men), "women": entries(women)}, "active": []}))


def _wiki_hit(transport, name):
    """A Wikipedia page image for `name` (add before `_ok_routes`: the first matching route wins)."""
    url = f"https://upload.wikimedia.org/{name.replace(' ', '_')}.jpg"
    transport.add_json(WIKIPEDIA_API_URL, {"query": {"pages": {"5": {"title": name, "thumbnail": {"source": url}}}}},
                       query=name)
    transport.add(url, Response(200, png_bytes(fmt="JPEG")))


def test_p4p_fighters_get_headshots_even_when_on_no_card(data, client, transport):
    _rankings(data, men=[("old1", "Retired Guy")], women=[("old2", "Other Retired")])
    _wiki_hit(transport, "Retired Guy")
    _wiki_hit(transport, "Other Retired")
    _ok_routes(transport)
    stage.process(data, client, NOW)
    for fid in ("old1", "old2"):
        assert (data / "images" / f"{fid}.webp").exists()
        assert _fighters(data)[fid]["image"] == f"images/{fid}.webp"


def test_a_p4p_fighter_missing_from_fighters_json_gets_the_file_only(data, client, transport):
    _rankings(data, men=[("p9", "Top Dog")])
    _wiki_hit(transport, "Top Dog")
    _ok_routes(transport)
    before = (data / "fighters.json").read_text()
    summary = stage.process(data, client, NOW)
    assert (data / "images" / "p9.webp").exists()
    assert "p9" not in _fighters(data)
    assert summary.hits == 3  # f1, f2 on the cards, and p9
    assert {k: v for k, v in _fighters(data).items() if k != "f1" and k != "f2"} == \
        {k: v for k, v in json.loads(before).items() if k != "f1" and k != "f2"}


def test_p4p_fighters_come_after_the_cards_and_count_against_the_limit(data, client, transport):
    _rankings(data, men=[("old1", "Retired Guy")])
    _ok_routes(transport)
    summary = stage.process(data, client, NOW, limit=3)  # f1, f2, f4 are on the cards
    assert summary.hits + summary.misses == 3
    assert "Retired Guy" not in _queried(transport)
    summary = stage.process(data, client, NOW, limit=1)  # f4's miss is recent: only old1 is left to try
    assert "Retired Guy" in _queried(transport)
    assert summary.misses == 1


def test_a_p4p_fighter_with_a_headshot_is_not_looked_up_again(data, client, transport):
    (data / "images").mkdir()
    (data / "images" / "p9.webp").write_bytes(b"x")
    _rankings(data, men=[("p9", "Top Dog"), ("f3", "Petr Yan")])  # p9 has its file, f3 has fighter.image
    _ok_routes(transport)
    stage.process(data, client, NOW)
    assert "Top Dog" not in _queried(transport) and "Petr Yan" not in _queried(transport)


def test_a_fighter_on_a_card_and_in_the_p4p_lists_is_looked_up_once(data, client, transport):
    _rankings(data, men=[("f1", "Alexander Volkanovski")])
    _ok_routes(transport)
    summary = stage.process(data, client, NOW)
    assert summary.hits == 2 and summary.misses == 1
    assert sum(url == headshot_url("3949584") for url, _ in transport.calls) == 1


def test_a_p4p_miss_is_recorded_and_not_retried_within_a_week(data, client, transport):
    _rankings(data, men=[("p9", "Top Dog")])
    _ok_routes(transport)
    stage.process(data, client, NOW)
    assert json.loads((data / "images" / "misses.json").read_text())["p9"] == "2026-10-24T12:00:00Z"
    assert not (data / "images" / "p9.webp").exists()
    transport.calls.clear()
    summary = stage.process(data, client, NOW)
    assert transport.calls == [] and summary.skipped == 2  # f4 and p9


@pytest.mark.parametrize("content", ["{not json", "[]", '{"as_of": "2026-10-23"}'], ids=["unparseable", "a list", "invalid"])
def test_an_unreadable_rankings_json_is_ignored(data, client, transport, content):
    """It's only a hint for which headshots to fetch: publish is what refuses a bad one."""
    (data / "rankings.json").write_text(content)
    _ok_routes(transport)
    summary = stage.process(data, client, NOW)
    assert summary.hits == 2 and summary.misses == 1


def test_run_exits_zero_with_a_p4p_list(data, client, transport, monkeypatch):
    _rankings(data, men=[("old1", "Retired Guy")])
    _ok_routes(transport)
    monkeypatch.setattr(stage, "make_client", lambda: client)
    assert stage.run(_args(data)) == 0
