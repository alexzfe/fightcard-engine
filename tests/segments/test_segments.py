import argparse
from datetime import date

import pytest
from fightcard import cli
from fightcard.images.http import Response
from fightcard.models import Event, Fight, Fighter
from fightcard.segments import espn, stage
from fightcard.store import Store
from segments_helpers import fixture_json

MOICANO_ESPN_ID = "600061420"
SCOREBOARD = fixture_json("scoreboard_calendar.json")
MOICANO_CARD = fixture_json("fightcenter_600061420.json")
UFC333_CARD = fixture_json("fightcenter_600061267.json")
FIGHTCENTER = espn.FIGHTCENTER_URL.format(event_id=MOICANO_ESPN_ID)

# Our side of the Moicano card: ufcstats names in ufcstats corner order.
MOICANO_BOUTS = [
    ("Renato Moicano", "Tom Nolan"),          # main event
    ("Yana Santos", "Luana Santos"),          # main card; shared surname
    ("Azamat Bekoev", "Andre Petroski"),       # prelims
    ("Zach Reese", "Mickey Gall"),
    ("Nobody Atall", "Someone Else"),         # not on ESPN's card
]


def _seed(tmp_path, bouts=MOICANO_BOUTS, status="upcoming", segments=None, name=None):
    store = Store(tmp_path)
    fighters, fights = {}, []
    for i, (red, blue) in enumerate(bouts):
        for fid, fname in ((f"r{i}", red), (f"b{i}", blue)):
            fighters[fid] = Fighter(id=fid, name=fname)
        seg = segments[i] if segments else None
        fights.append(Fight(id=f"f{i}", red=f"r{i}", blue=f"b{i}", segment=seg))
    store.save_fighters(fighters)
    store.save_event(Event(
        id="0a66de3a797cf73a", slug="ufc-fight-night-moicano-vs-nolan",
        name=name or "UFC Fight Night: Moicano vs. Nolan", date="2026-10-31",
        status=status, fights=fights,
    ))
    return store


def _segments(store):
    return [f.segment for f in store.load_event("0a66de3a797cf73a").fights]


# --- contract ----------------------------------------------------------------------


def test_fight_segment_defaults_to_none_and_rejects_unknown_values():
    assert Fight(id="x", red="a", blue="b").segment is None
    for ok in ("main", "prelims", "early_prelims"):
        assert Fight(id="x", red="a", blue="b", segment=ok).segment == ok
    with pytest.raises(ValueError):
        Fight(id="x", red="a", blue="b", segment="prelims1")


def test_segments_stage_is_registered_in_the_cli():
    assert cli.STAGES["segments"] == "fightcard.segments.stage"


# --- ESPN parsing --------------------------------------------------------------------


def test_segment_names_map_to_contract_values():
    assert espn.segment_of("main") == "main"
    assert espn.segment_of("prelims1") == "prelims"
    assert espn.segment_of("prelims2") == "early_prelims"
    assert espn.segment_of("ppv") is None
    assert espn.segment_of(None) is None


def test_parse_calendar_extracts_event_ids_names_and_dates():
    cal = espn.parse_calendar(SCOREBOARD)
    by_id = {c.espn_id: c for c in cal}
    moicano = by_id[MOICANO_ESPN_ID]
    assert moicano.label == "UFC Fight Night: Moicano vs. Nolan"
    assert moicano.start == date(2026, 11, 1)  # 00:00Z the night after the local date


def test_parse_calendar_tolerates_junk():
    assert espn.parse_calendar({}) == []
    assert espn.parse_calendar({"leagues": [{"calendar": [{"label": "x"}, "nope"]}]}) == []


@pytest.mark.parametrize(
    ("name", "day", "expected"),
    [
        ("UFC Fight Night: Moicano vs. Nolan", "2026-10-31", MOICANO_ESPN_ID),   # +1 day in UTC
        ("UFC Fight Night: Prochazka vs. Stirling", "2026-11-21", "600061665"),  # accent on ESPN
        ("UFC 334: Gane vs. Hokit", "2026-11-14", "600061400"),
        ("UFC Fight Night: Moicano vs. Nolan", "2026-10-28", None),              # date too far
        ("UFC Fight Night: Somebody vs. Else", "2026-10-31", None),             # name mismatch
    ],
)
def test_match_event_needs_date_and_name(name, day, expected):
    ev = Event(id="e", slug="e", name=name, date=day, status="upcoming", fights=[])
    assert espn.match_event(ev, espn.parse_calendar(SCOREBOARD)) == expected


def test_parse_card_lists_every_bout_with_its_segment():
    bouts = espn.parse_card(UFC333_CARD)
    segs = [b.segment for b in bouts]
    assert segs.count("main") == 5
    assert segs.count("prelims") == 4
    assert segs.count("early_prelims") == 5
    first = next(b for b in bouts if "Alexander Volkanovski" in b.names)
    assert first.segment == "main" and "Movsar Evloev" in first.names


def test_parse_card_tolerates_junk():
    assert espn.parse_card({}) == []
    assert espn.parse_card({"cards": {"main": {"competitions": [{"competitors": [{}]}]}}}) == []


# --- stage ---------------------------------------------------------------------------


def _wire(transport, card=MOICANO_CARD):
    transport.add_json(espn.SCOREBOARD_URL, SCOREBOARD)
    transport.add_json(FIGHTCENTER, card)


def test_process_sets_segments_matching_either_corner_order(tmp_path, transport, client):
    store = _seed(tmp_path)
    _wire(transport)
    summary = stage.process(tmp_path, client)
    # ESPN lists Nolan first; Yana/Luana Santos must not cross-match.
    assert _segments(store) == ["main", "main", "prelims", "prelims", None]
    assert (summary.events, summary.fights, summary.unmatched) == (1, 4, 1)


def test_process_moves_a_fight_espn_now_places_elsewhere(tmp_path, transport, client):
    store = _seed(tmp_path, segments=["prelims", "main", "main", "main", "main"])
    _wire(transport)
    stage.process(tmp_path, client)
    assert _segments(store) == ["main", "main", "prelims", "prelims", None]


@pytest.mark.parametrize(
    "failure",
    [
        "scoreboard_down",
        "fightcenter_down",
        "fightcenter_raises",
        "fightcenter_bad_json",
    ],
)
def test_espn_failures_keep_known_segments_and_exit_zero(tmp_path, transport, client, failure):
    known = ["main", "main", "prelims", "early_prelims", "prelims"]
    store = _seed(tmp_path, segments=known)
    if failure != "scoreboard_down":
        transport.add_json(espn.SCOREBOARD_URL, SCOREBOARD)
    if failure == "fightcenter_raises":
        transport.add(FIGHTCENTER, OSError("connection reset"))
    elif failure == "fightcenter_bad_json":
        transport.add(FIGHTCENTER, Response(200, b"<html>"))
    summary = stage.process(tmp_path, client)
    assert _segments(store) == known
    assert summary.fights == 0


def test_unmatched_event_is_left_alone(tmp_path, transport, client):
    store = _seed(tmp_path, name="UFC Fight Night: Somebody vs. Else", segments=["main"] * 5)
    _wire(transport)
    summary = stage.process(tmp_path, client)
    assert _segments(store) == ["main"] * 5
    assert summary.events == 0
    assert not any(c.startswith(espn.FIGHTCENTER_URL.split("{")[0]) for c in transport.calls)


def test_completed_events_are_skipped(tmp_path, transport, client):
    store = _seed(tmp_path, status="completed", segments=["prelims"] * 5)
    _wire(transport)
    stage.process(tmp_path, client)
    assert _segments(store) == ["prelims"] * 5
    assert transport.calls == []  # no upcoming events, no requests


def test_unchanged_event_is_not_rewritten(tmp_path, transport, client):
    store = _seed(tmp_path, segments=["main", "main", "prelims", "prelims", None])
    path = store.events_dir / "0a66de3a797cf73a.json"
    before = path.stat().st_mtime_ns
    _wire(transport)
    stage.process(tmp_path, client)
    assert path.stat().st_mtime_ns == before


def test_run_exits_zero_on_network_failure_and_one_on_missing_data(tmp_path, monkeypatch, client):
    _seed(tmp_path)
    monkeypatch.setattr(stage, "make_client", lambda: client)  # every URL 404s
    assert stage.run(argparse.Namespace(data=tmp_path)) == 0
    assert stage.run(argparse.Namespace(data=tmp_path / "missing")) == 1
