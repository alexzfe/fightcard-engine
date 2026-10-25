import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fightcard.models import Event, Fight, Fighter, Index, ModelInfo
from fightcard.store import Store
from fightcard.ufcstats import stage
from fightcard.ufcstats.client import Halt, HttpError
from fightcard.ufcstats.parsers import parse_event_detail, parse_event_list

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ufcstats"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
NOW_ISO = "2026-10-15T12:00:00Z"
BASE = "http://ufcstats.com"
UPCOMING_IDS = [e.id for e in parse_event_list(read_fixture("upcoming.html"))]
COMPLETED_ID = "7f98d9d5a10fa25c"
UPCOMING_CARD = parse_event_detail(read_fixture("event_detail_upcoming.html"), "x")
COMPLETED_CARD = parse_event_detail(read_fixture("event_detail_completed.html"), COMPLETED_ID)


class FakeClient:
    def __init__(self, pages: dict[str, str], fighter_page: str | None = None):
        self.pages = pages
        self.fighter_page = fighter_page
        self.urls: list[str] = []

    def get(self, url: str) -> str:
        self.urls.append(url)
        if url in self.pages:
            return self.pages[url]
        if "/fighter-details/" in url and self.fighter_page is not None:
            return self.fighter_page
        raise HttpError(url, 404)


def empty_card() -> str:
    """The upcoming detail page with its fight table removed (no bouts announced)."""
    return read_fixture("event_detail_upcoming.html").split('<tbody class="b-fight-details__table-body">')[0]


def site_pages(upcoming=None, detail=None, empty=()):
    pages = {
        stage.UPCOMING_URL: upcoming if upcoming is not None else read_fixture("upcoming.html"),
        stage.COMPLETED_URL: read_fixture("completed.html"),
        f"{BASE}/event-details/{COMPLETED_ID}": read_fixture("event_detail_completed.html"),
    }
    for event_id in UPCOMING_IDS:
        pages[f"{BASE}/event-details/{event_id}"] = (
            detail if detail is not None else read_fixture("event_detail_upcoming.html")
        )
    for event_id in empty:
        pages[f"{BASE}/event-details/{event_id}"] = empty_card()
    return pages


def make_client(**kw):
    return FakeClient(site_pages(**kw), fighter_page=read_fixture("fighter.html"))


def scrape(tmp_path, client, recent=1):
    return stage.scrape(Store(tmp_path), client, now=NOW, recent=recent)


def fighter_urls(client):
    return [u for u in client.urls if "/fighter-details/" in u]


# --- happy path ---------------------------------------------------------------


def test_fresh_scrape_writes_events_fighters_and_index(tmp_path):
    client = make_client()
    assert scrape(tmp_path, client) == 0

    store = Store(tmp_path)
    events = store.load_events()
    assert set(events) == set(UPCOMING_IDS) | {COMPLETED_ID}

    first = events[UPCOMING_IDS[0]]
    assert first.status == "upcoming"
    assert first.name == "UFC Fight Night: Moicano vs. Nolan"
    assert len(first.fights) == 13
    # list metadata wins over the detail page (each event keeps its own name/date)
    third = events[UPCOMING_IDS[2]]
    assert (third.name, third.date, third.slug) == (
        "UFC 334: Gane vs. Hokit", "2026-11-14", "ufc-334-gane-vs-hokit"
    )

    done = events[COMPLETED_ID]
    assert done.status == "completed"
    assert all(f.result is not None for f in done.fights)

    index = store.load_index()
    assert index.upcoming == UPCOMING_IDS
    assert index.recent == [COMPLETED_ID]
    assert index.generated_at == NOW_ISO
    assert index.model is None

    fighters = store.load_fighters()
    expected = set(UPCOMING_CARD.fighters) | set(COMPLETED_CARD.fighters)
    assert set(fighters) == expected
    assert all(f.details_fetched_at == NOW_ISO for f in fighters.values())
    assert fighters["b6452706b373eea1"].height_in == 71.0
    # each fighter page fetched once even though they appear on 7 cards
    assert len(fighter_urls(client)) == len(expected)


def test_event_files_follow_contract_formatting(tmp_path):
    scrape(tmp_path, make_client())
    text = (tmp_path / "events" / f"{UPCOMING_IDS[0]}.json").read_text()
    assert text.endswith("}\n")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_recent_skips_next_row_and_future_events(tmp_path):
    client = make_client()
    scrape(tmp_path, client)
    # completed.html row 1 is Buckley vs. Malott (2026-10-17), after NOW → skipped
    assert f"{BASE}/event-details/55e94a9b525dcf45" not in client.urls


def test_recent_zero_skips_completed_listing(tmp_path):
    client = make_client()
    scrape(tmp_path, client, recent=0)
    assert stage.COMPLETED_URL not in client.urls
    assert COMPLETED_ID not in Store(tmp_path).load_events()


# --- validation ---------------------------------------------------------------


def test_zero_upcoming_events_fails_without_writing(tmp_path):
    client = make_client(upcoming="<html><body>no table</body></html>")
    assert scrape(tmp_path, client) != 0
    assert list(tmp_path.iterdir()) == []


def test_all_upcoming_events_without_fights_fails_without_writing(tmp_path):
    assert scrape(tmp_path, make_client(detail=empty_card())) != 0
    assert list(tmp_path.iterdir()) == []


def test_soonest_upcoming_event_without_fights_fails_without_writing(tmp_path, capsys):
    assert scrape(tmp_path, make_client(empty=[UPCOMING_IDS[0]])) != 0
    assert list(tmp_path.iterdir()) == []
    assert UPCOMING_IDS[0] in capsys.readouterr().err


def test_far_out_events_without_fights_are_skipped(tmp_path, caplog):
    far = UPCOMING_IDS[5:]  # UFC 336 and the Sydney Fight Night: no bouts announced
    caplog.set_level("INFO", logger="fightcard.ufcstats.stage")
    assert scrape(tmp_path, make_client(empty=far)) == 0

    store = Store(tmp_path)
    for event_id in far:
        assert store.load_event(event_id) is None
    assert store.load_index().upcoming == UPCOMING_IDS[:5]
    skipped = [r for r in caplog.records if r.levelname == "INFO" and "0 fights" in r.getMessage()]
    assert sorted(r.getMessage().split()[2] for r in skipped) == sorted(far)


def test_previously_written_event_now_empty_is_left_alone_and_unindexed(tmp_path):
    old = UPCOMING_CARD.event.model_copy(update={"id": UPCOMING_IDS[6], "date": "2027-02-06"})
    store = seed(tmp_path, old)
    assert scrape(tmp_path, make_client(empty=[UPCOMING_IDS[6]])) == 0
    assert store.load_event(UPCOMING_IDS[6]) == old
    assert UPCOMING_IDS[6] not in store.load_index().upcoming


def test_halt_fails_without_writing(tmp_path):
    class Blocked:
        def get(self, url):
            raise Halt("blocked")

    assert scrape(tmp_path, Blocked()) != 0
    assert list(tmp_path.iterdir()) == []


def test_halt_prints_one_line_error(tmp_path, capsys):
    class Blocked:
        def get(self, url):
            raise Halt("HTTP 429 twice in a row")

    assert scrape(tmp_path, Blocked()) == 1
    err = capsys.readouterr().err.strip()
    assert err.count("\n") == 0
    assert "HTTP 429 twice in a row" in err
    assert "Traceback" not in err


def test_event_page_http_error_fails_without_writing(tmp_path):
    pages = site_pages()
    del pages[f"{BASE}/event-details/{UPCOMING_IDS[1]}"]
    assert scrape(tmp_path, FakeClient(pages, read_fixture("fighter.html"))) != 0
    assert list(tmp_path.iterdir()) == []


# --- merge, never clobber --------------------------------------------------------


def seed(tmp_path, *events, fighters=None, index=None):
    store = Store(tmp_path)
    for e in events:
        store.save_event(e)
    if fighters is not None:
        store.save_fighters(fighters)
    if index is not None:
        store.save_index(index)
    return store


def test_keeps_tiers_for_fights_still_on_the_card(tmp_path):
    main = UPCOMING_CARD.event.fights[0]
    old = Event(
        id=UPCOMING_IDS[0], slug="old", name="old", date="2026-10-31", status="upcoming",
        fights=[
            main.model_copy(update={"finish_tier": "high", "tier_frozen_at": "2026-10-31T00:00:00Z"}),
            Fight(id="cancelled-bout", red="a", blue="b", finish_tier="low"),
        ],
    )
    store = seed(tmp_path, old)
    assert scrape(tmp_path, make_client()) == 0

    new = store.load_event(UPCOMING_IDS[0])
    assert new.fights[0].finish_tier == "high"
    assert new.fights[0].tier_frozen_at == "2026-10-31T00:00:00Z"
    assert "cancelled-bout" not in [f.id for f in new.fights]
    assert all(f.finish_tier is None for f in new.fights[1:])


def test_fresh_fighters_are_not_refetched_and_images_are_kept(tmp_path):
    fresh = (NOW - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (NOW - timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fighters = {
        "b6452706b373eea1": Fighter(id="b6452706b373eea1", name="Kept", image="images/m.png",
                                    details_fetched_at=fresh),
        "6c3b2525436cf5d4": Fighter(id="6c3b2525436cf5d4", name="Old", image="images/n.png",
                                    details_fetched_at=stale),
        "unrelated": Fighter(id="unrelated", name="Not on any card"),
    }
    store = seed(tmp_path, fighters=fighters)
    client = make_client()
    scrape(tmp_path, client)

    urls = fighter_urls(client)
    assert f"{BASE}/fighter-details/b6452706b373eea1" not in urls
    assert f"{BASE}/fighter-details/6c3b2525436cf5d4" in urls

    out = store.load_fighters()
    assert out["b6452706b373eea1"].name == "Kept"
    assert out["6c3b2525436cf5d4"].image == "images/n.png"
    assert out["6c3b2525436cf5d4"].details_fetched_at == NOW_ISO
    assert "unrelated" in out


def test_fighter_page_error_falls_back_to_card_name(tmp_path):
    client = FakeClient(site_pages(), fighter_page=None)
    assert scrape(tmp_path, client, recent=0) == 0
    nolan = Store(tmp_path).load_fighters()["6c3b2525436cf5d4"]
    assert nolan.name == "Tom Nolan"
    assert nolan.details_fetched_at is None


def test_index_keeps_model_info(tmp_path):
    model = ModelInfo(as_of="2026-10-01", version="v1")
    store = seed(tmp_path, index=Index(generated_at="x", upcoming=[], recent=[], model=model))
    scrape(tmp_path, make_client())
    assert store.load_index().model == model


# --- results -------------------------------------------------------------------


def past_upcoming_event(**fight_updates):
    """COMPLETED_ID as it looked before fight night: upcoming, corners in card order."""
    fights = []
    for f in COMPLETED_CARD.event.fights:
        # before the event, ufcstats listed the eventual loser of fight 0 in the red corner
        red, blue = (f.blue, f.red) if f is COMPLETED_CARD.event.fights[0] else (f.red, f.blue)
        fights.append(f.model_copy(update={"red": red, "blue": blue, "result": None, **fight_updates}))
    return COMPLETED_CARD.event.model_copy(update={"status": "upcoming", "fights": fights})


def test_past_upcoming_event_is_rescraped_as_completed(tmp_path):
    store = seed(tmp_path, past_upcoming_event(finish_tier="medium", tier_frozen_at="2026-10-10T00:00:00Z"))
    client = make_client()
    assert scrape(tmp_path, client, recent=0) == 0

    done = store.load_event(COMPLETED_ID)
    assert done.status == "completed"
    main = done.fights[0]
    assert main.finish_tier == "medium"
    assert main.tier_frozen_at == "2026-10-10T00:00:00Z"
    # corners keep their pre-fight orientation; winner is still the right fighter
    assert (main.red, main.blue) == (COMPLETED_CARD.event.fights[0].blue, COMPLETED_CARD.event.fights[0].red)
    assert main.result.winner == COMPLETED_CARD.event.fights[0].red
    assert store.load_index().recent == [COMPLETED_ID]


def test_event_page_fetched_once_when_both_stale_and_recent(tmp_path):
    seed(tmp_path, past_upcoming_event())
    client = make_client()
    scrape(tmp_path, client, recent=1)
    assert client.urls.count(f"{BASE}/event-details/{COMPLETED_ID}") == 1


def test_newly_completed_event_refreshes_its_fighters(tmp_path):
    fresh = (NOW - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    winner = COMPLETED_CARD.event.fights[0].red
    seed(tmp_path, past_upcoming_event(), fighters={winner: Fighter(id=winner, name="W", details_fetched_at=fresh)})
    client = make_client()
    scrape(tmp_path, client, recent=0)
    assert f"{BASE}/fighter-details/{winner}" in fighter_urls(client)


def test_already_completed_event_does_not_refresh_fresh_fighters(tmp_path):
    fresh = (NOW - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fighters = {fid: Fighter(id=fid, name=n, details_fetched_at=fresh) for fid, n in COMPLETED_CARD.fighters.items()}
    seed(tmp_path, COMPLETED_CARD.event, fighters=fighters)
    client = make_client()
    scrape(tmp_path, client, recent=1)
    assert not set(fighter_urls(client)) & {f"{BASE}/fighter-details/{fid}" for fid in fighters}


def test_past_event_without_results_stays_upcoming_but_leaves_index(tmp_path):
    no_results = read_fixture("event_detail_upcoming.html")
    stale = past_upcoming_event()
    store = seed(tmp_path, stale)
    pages = site_pages()
    pages[f"{BASE}/event-details/{COMPLETED_ID}"] = no_results
    assert scrape(tmp_path, FakeClient(pages, read_fixture("fighter.html")), recent=0) == 0
    kept = store.load_event(COMPLETED_ID)
    assert kept.status == "upcoming"
    assert COMPLETED_ID not in store.load_index().upcoming


def test_future_event_no_longer_listed_is_left_alone(tmp_path):
    ghost = Event(id="ghost", slug="g", name="Ghost", date="2027-05-01", status="upcoming",
                  fights=[Fight(id="g1", red="a", blue="b")])
    store = seed(tmp_path, ghost)
    client = make_client()
    scrape(tmp_path, client)
    assert store.load_event("ghost") == ghost
    assert f"{BASE}/event-details/ghost" not in client.urls


def test_recent_lists_completed_events_most_recent_first(tmp_path):
    older = COMPLETED_CARD.event.model_copy(update={"id": "older", "date": "2026-09-01"})
    store = seed(tmp_path, older)
    scrape(tmp_path, make_client())
    assert store.load_index().recent == [COMPLETED_ID, "older"]


# --- CLI wiring --------------------------------------------------------------------


def test_add_arguments_defaults(tmp_path):
    parser = argparse.ArgumentParser()
    stage.add_arguments(parser)
    args = parser.parse_args([])
    assert args.recent == 1
    assert args.cookie_file is None
    assert parser.parse_args(["--recent", "3", "--cookie-file", "c.json"]).recent == 3


def test_run_builds_client_and_scrapes(tmp_path, monkeypatch):
    seen = {}

    def fake_client(cookie_file):
        seen["cookie_file"] = cookie_file
        return make_client()

    monkeypatch.setattr(stage, "make_client", fake_client)
    args = argparse.Namespace(data=tmp_path, recent=0, cookie_file=tmp_path / "c.json")
    assert stage.run(args) == 0
    assert seen["cookie_file"] == tmp_path / "c.json"
    assert (tmp_path / "index.json").exists()


def test_cli_dispatches_scrape(tmp_path, monkeypatch):
    from fightcard import cli

    monkeypatch.setattr(stage, "make_client", lambda cookie_file: make_client())
    assert cli.main(["--data", str(tmp_path), "scrape", "--recent", "0"]) == 0


def test_make_client_uses_cookie_file(tmp_path):
    client = stage.make_client(tmp_path / "c.json")
    assert client.cookie_file == tmp_path / "c.json"
    assert stage.make_client(None).cookie_file.name == "ufcstats_cookie.json"


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    monkeypatch.setattr(stage, "utcnow", lambda: NOW)
