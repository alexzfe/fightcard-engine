"""`fightcard scrape --live`: on fight days, the live event's own page only, every few minutes (docs/SERVICE.md).

It refetches each indexed event dated today or yesterday (UTC) that isn't fully resulted, and nothing else: no
listings, no fighter pages, no other cards. It writes only when a result changed, and then moves index.generated_at;
otherwise it exits `live.UNCHANGED` and leaves data/ and the store alone, so the runner skips score and publish."""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from test_scrape_stage import BASE, UPCOMING_IDS, FakeClient, delisted, make_client, read_fixture, site_pages

from fightcard.cli import main
from fightcard.history.ingest import ingest
from fightcard.history.store import connect
from fightcard.models import Event, Fight, Index, Result
from fightcard.publish.build import build
from fightcard.service import live
from fightcard.store import Store
from fightcard.ufcstats import stage
from fightcard.ufcstats.client import Halt, HttpError
from fightcard.ufcstats.parsers import parse_event_detail

HISTORY_HTML = Path(__file__).resolve().parent.parent / "fixtures" / "history" / "html"
LIVE_ID = UPCOMING_IDS[0]  # UFC Fight Night: Moicano vs. Nolan, 2026-10-31
LIVE_URL = f"{BASE}/event-details/{LIVE_ID}"
MORNING = datetime(2026, 10, 31, 9, 17, tzinfo=UTC)
EVENING = datetime(2026, 10, 31, 23, 5, tzinfo=UTC)
LATER = datetime(2026, 10, 31, 23, 10, tzinfo=UTC)
EVENING_ISO = "2026-10-31T23:05:00Z"
CARD = parse_event_detail(read_fixture("event_detail_completed.html"), LIVE_ID).event  # 13 bouts, all resulted


def card(resulted: int, no_outcome: tuple[int, ...] = ()) -> str:
    """The completed fixture as LIVE_ID's page on fight night: the first `resulted` bouts in the table have results,
    the rest none yet (no W/L flag, method, round or time); `no_outcome` rows keep their method but lose the flag."""
    soup = BeautifulSoup(read_fixture("event_detail_completed.html").replace("October 10, 2026", "October 31, 2026"),
                         "lxml")
    for i, row in enumerate(soup.select("tbody.b-fight-details__table-body tr.b-fight-details__table-row")):
        cols = row.find_all("td")
        if i >= resulted:
            for c in (0, 7, 8, 9):
                cols[c].clear()
        elif i in no_outcome:
            cols[0].clear()
    return str(soup)


def morning(tmp_path) -> Store:
    """data/ as the hourly run left it the morning of 2026-10-31: LIVE_ID upcoming and indexed."""
    store = Store(tmp_path)
    assert stage.scrape(store, make_client(), now=MORNING, recent=0) == 0
    return store


def live_scrape(store: Store, pages: dict[str, str], now: datetime = EVENING, history=None) -> tuple[int, FakeClient]:
    client = FakeClient(pages)
    return stage.scrape_live(store, client, now=now, history=history), client


def tonight(page: str) -> dict[str, str]:
    """The site for an hourly run on fight night: the card off the upcoming listing (it has started), `page` its page."""
    pages = site_pages(upcoming=delisted(LIVE_ID))
    pages[LIVE_URL] = page
    return pages


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def seeded_store(tmp_path):
    conn = connect(tmp_path / "history" / "history.sqlite")
    ingest(conn, HISTORY_HTML)
    store = Store(tmp_path / "data")
    assert stage.scrape(store, make_client(), now=MORNING, recent=0, history=conn) == 0
    return store, conn


# --- what it fetches --------------------------------------------------------------------------------------------


def test_it_fetches_the_live_event_s_page_and_nothing_else(tmp_path):
    store = morning(tmp_path)
    code, client = live_scrape(store, {LIVE_URL: card(resulted=2)})
    assert code == 0
    assert client.urls == [LIVE_URL]


def test_new_results_are_written_and_generated_at_moves(tmp_path):
    store = morning(tmp_path)
    code, _ = live_scrape(store, {LIVE_URL: card(resulted=2)})
    assert code == 0
    event = store.load_event(LIVE_ID)
    assert [f.result is not None for f in event.fights] == [True, True] + [False] * 11
    assert event.status == "completed"
    assert store.load_index().generated_at == EVENING_ISO


def test_nothing_new_writes_nothing_and_says_unchanged(tmp_path):
    store = morning(tmp_path)
    assert live_scrape(store, {LIVE_URL: card(resulted=2)})[0] == 0
    before = tree(tmp_path)
    code, client = live_scrape(store, {LIVE_URL: card(resulted=2)}, now=LATER)
    assert code == live.UNCHANGED
    assert client.urls == [LIVE_URL]
    assert tree(tmp_path) == before  # generated_at included: clients can tell nothing moved


def test_a_card_without_results_yet_is_unchanged(tmp_path):
    store = morning(tmp_path)
    before = tree(tmp_path)
    code, _ = live_scrape(store, {LIVE_URL: card(resulted=0)})
    assert code == live.UNCHANGED
    assert tree(tmp_path) == before


def test_with_no_live_event_it_fetches_nothing(tmp_path):
    store = morning(tmp_path)
    code, client = live_scrape(store, {}, now=datetime(2026, 10, 29, 23, 5, tzinfo=UTC))  # two days before the card
    assert code == live.UNCHANGED
    assert client.urls == []


def test_the_day_after_it_still_follows_a_card_awaiting_results(tmp_path):
    # US cards end after midnight UTC
    store = morning(tmp_path)
    code, client = live_scrape(store, {LIVE_URL: card(resulted=13)}, now=datetime(2026, 11, 1, 4, 30, tzinfo=UTC))
    assert code == 0
    assert client.urls == [LIVE_URL]


def test_two_days_after_it_stops(tmp_path):
    store = morning(tmp_path)
    code, client = live_scrape(store, {LIVE_URL: card(resulted=13)}, now=datetime(2026, 11, 2, 0, 5, tzinfo=UTC))
    assert code == live.UNCHANGED
    assert client.urls == []


def test_a_fully_resulted_card_is_not_fetched_again(tmp_path):
    store = morning(tmp_path)
    assert live_scrape(store, {LIVE_URL: card(resulted=13)})[0] == 0
    code, client = live_scrape(store, {LIVE_URL: card(resulted=13)}, now=LATER)
    assert code == live.UNCHANGED
    assert client.urls == []


# --- data/ --------------------------------------------------------------------------------------------------------


def test_a_card_with_results_moves_to_recent(tmp_path):
    store = morning(tmp_path)
    live_scrape(store, {LIVE_URL: card(resulted=1)})
    index = store.load_index()
    assert index.recent[0] == LIVE_ID
    assert index.upcoming == UPCOMING_IDS[1:]


def test_a_new_fighter_on_the_card_gets_a_stub_so_publish_can_build(tmp_path):
    store = morning(tmp_path)
    live_scrape(store, {LIVE_URL: card(resulted=1)})
    fighters = store.load_fighters()
    assert {fid for f in store.load_event(LIVE_ID).fights for fid in (f.red, f.blue)} <= fighters.keys()
    assert fighters[CARD.fights[0].red].name == "Brendan Allen"
    assert f"events/{LIVE_ID}.json" in build(tmp_path)


def test_fighters_already_in_data_are_left_as_they_are(tmp_path):
    store = morning(tmp_path)
    before = store.load_fighters()
    live_scrape(store, {LIVE_URL: card(resulted=1)})
    after = store.load_fighters()
    assert {fid: after[fid] for fid in before} == before


def test_tiers_and_segments_are_kept(tmp_path):
    store = morning(tmp_path)
    event = store.load_event(LIVE_ID)
    # the card as fight night shows it: these bouts, with tiers frozen and segments from ESPN
    frozen = [f.model_copy(update={"finish_tier": "high", "tier_frozen_at": "2026-10-31T00:17:00Z", "segment": "main",
                                   "result": None})
              for f in CARD.fights]
    store.save_event(event.model_copy(update={"fights": frozen}))
    live_scrape(store, {LIVE_URL: card(resulted=3)})
    fights = store.load_event(LIVE_ID).fights
    assert all((f.finish_tier, f.tier_frozen_at, f.segment) == ("high", "2026-10-31T00:17:00Z", "main") for f in fights)


# --- a bout with a method but no W/L outcome ---------------------------------------------------------------------


def test_a_bout_with_a_method_but_no_outcome_is_held_back_not_fatal(tmp_path, caplog):
    store = morning(tmp_path)
    with caplog.at_level(logging.INFO, logger="fightcard.ufcstats.stage"):
        code, _ = live_scrape(store, {LIVE_URL: card(resulted=3, no_outcome=(2,))})
    assert code == 0
    results = [f.result for f in store.load_event(LIVE_ID).fights]
    assert results[0] is not None and results[1] is not None
    assert results[2] is None  # not resulted yet: picked up once the flag is on the page
    assert CARD.fights[2].id in caplog.text and "no W/L outcome" in caplog.text


def test_a_held_bout_comes_in_once_its_outcome_is_posted(tmp_path):
    store = morning(tmp_path)
    live_scrape(store, {LIVE_URL: card(resulted=3, no_outcome=(2,))})
    code, _ = live_scrape(store, {LIVE_URL: card(resulted=3)}, now=LATER)
    assert code == 0
    assert store.load_event(LIVE_ID).fights[2].result.outcome == "win"


# --- when each result was first seen -------------------------------------------------------------------------------


def seen_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("result_seen ")]


def test_each_result_s_first_sighting_is_logged_once(tmp_path, caplog):
    store = morning(tmp_path)
    with caplog.at_level(logging.INFO, logger="fightcard.ufcstats.stage"):
        live_scrape(store, {LIVE_URL: card(resulted=2)})
        live_scrape(store, {LIVE_URL: card(resulted=3)}, now=LATER)
    assert seen_lines(caplog) == [
        f"result_seen event={LIVE_ID} fight={CARD.fights[0].id} seen_at={EVENING_ISO}",
        f"result_seen event={LIVE_ID} fight={CARD.fights[1].id} seen_at={EVENING_ISO}",
        f"result_seen event={LIVE_ID} fight={CARD.fights[2].id} seen_at=2026-10-31T23:10:00Z",
    ]


def test_the_hourly_scrape_logs_first_sightings_too(tmp_path, caplog):
    morning(tmp_path)
    pages = tonight(card(resulted=1))
    with caplog.at_level(logging.INFO, logger="fightcard.ufcstats.stage"):
        assert stage.scrape(Store(tmp_path), FakeClient(pages, read_fixture("fighter.html")), now=EVENING,
                            recent=0) == 0
    assert seen_lines(caplog) == [f"result_seen event={LIVE_ID} fight={CARD.fights[0].id} seen_at={EVENING_ISO}"]


# --- failures ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("error", [HttpError(LIVE_URL, 503), Halt("challenge not solved")])
def test_a_fetch_error_fails_and_writes_nothing(tmp_path, error, capsys):
    store = morning(tmp_path)
    before = tree(tmp_path)

    class Failing:
        def get(self, url):
            raise error

    assert stage.scrape_live(store, Failing(), now=EVENING) == 1
    assert tree(tmp_path) == before
    assert "nothing written" in capsys.readouterr().err


def test_a_page_with_no_bouts_is_left_alone(tmp_path):
    # a glitch mid-event, or every bout cancelled: the hourly run, which has the listing, settles it
    store = morning(tmp_path)
    before = tree(tmp_path)
    empty = read_fixture("event_detail_upcoming.html").split('<tbody class="b-fight-details__table-body">')[0]
    code, _ = live_scrape(store, {LIVE_URL: empty})
    assert code == live.UNCHANGED
    assert tree(tmp_path) == before


# --- the history store ----------------------------------------------------------------------------------------------


def test_results_go_into_the_history_store_too(tmp_path):
    store, conn = seeded_store(tmp_path)
    is_ufc, source = conn.execute("SELECT is_ufc, source FROM events WHERE event_id = ?", (LIVE_ID,)).fetchone()
    code, _ = live_scrape(store, {LIVE_URL: card(resulted=2)}, history=conn)
    assert code == 0
    rows = conn.execute("SELECT outcome FROM fights WHERE event_id = ? ORDER BY position", (LIVE_ID,)).fetchall()
    assert [r[0] for r in rows] == ["win", "win"] + [None] * 11
    assert conn.execute("SELECT status, is_ufc, source FROM events WHERE event_id = ?", (LIVE_ID,)).fetchone() == (
        "completed", is_ufc, source)
    assert conn.execute("SELECT fetched_at FROM pages WHERE url = ?", (LIVE_URL,)).fetchone()[0] == EVENING_ISO
    conn.close()


def test_unchanged_leaves_the_history_store_alone(tmp_path):
    store, conn = seeded_store(tmp_path)
    live_scrape(store, {LIVE_URL: card(resulted=2)}, history=conn)
    before = conn.execute("SELECT * FROM pages ORDER BY url").fetchall()
    assert live_scrape(store, {LIVE_URL: card(resulted=2)}, now=LATER, history=conn)[0] == live.UNCHANGED
    assert conn.execute("SELECT * FROM pages ORDER BY url").fetchall() == before
    conn.close()


# --- the hourly run, on fight night ---------------------------------------------------------------------------------


def test_the_hourly_run_holds_an_outcome_less_bout_on_tonight_s_card_instead_of_failing(tmp_path):
    store, conn = seeded_store(tmp_path)
    pages = tonight(card(resulted=3, no_outcome=(2,)))
    assert stage.scrape(store, FakeClient(pages, read_fixture("fighter.html")), now=EVENING, recent=0,
                        history=conn) == 0
    assert store.load_event(LIVE_ID).fights[2].result is None
    assert conn.execute("SELECT outcome FROM fights WHERE fight_id = ?", (CARD.fights[2].id,)).fetchone() == (None,)
    conn.close()


def test_the_hourly_run_refreshes_fighters_whose_bout_the_live_loop_already_completed(tmp_path):
    # the live loop marks the card completed without fetching fighter pages; the next hourly run still has to refresh
    # the record of everyone who has fought since their page was fetched, and only theirs
    store = morning(tmp_path)
    live_scrape(store, {LIVE_URL: card(resulted=2)})
    fighters = store.load_fighters()
    for f in CARD.fights:
        for fid in (f.red, f.blue):  # fetched this morning: fresh, with no bout on 2026-10-31 yet
            fighters[fid] = fighters[fid].model_copy(update={"details_fetched_at": "2026-10-31T09:17:00Z",
                                                             "recent_fights": []})
    store.save_fighters(fighters)
    pages = tonight(card(resulted=2))
    client = FakeClient(pages, read_fixture("fighter.html"))
    assert stage.scrape(store, client, now=datetime(2026, 10, 31, 23, 17, tzinfo=UTC), recent=0) == 0
    fetched = {u.rsplit("/", 1)[1] for u in client.urls if "/fighter-details/" in u}
    assert {fid for f in CARD.fights[:2] for fid in (f.red, f.blue)} <= fetched
    assert not {fid for f in CARD.fights[2:] for fid in (f.red, f.blue)} & fetched


# --- CLI ---------------------------------------------------------------------------------------------------------


def test_cli_live_runs_the_live_scrape(tmp_path, monkeypatch):
    morning(tmp_path)
    monkeypatch.setattr(stage, "make_client", lambda cookie_file: FakeClient({LIVE_URL: card(resulted=1)}))
    monkeypatch.setattr(stage, "utcnow", lambda: EVENING)
    assert main(["--data", str(tmp_path), "scrape", "--live", "--no-history"]) == 0
    assert main(["--data", str(tmp_path), "scrape", "--live", "--no-history"]) == live.UNCHANGED


def test_add_arguments_has_live():
    parser = argparse.ArgumentParser()
    stage.add_arguments(parser)
    assert parser.parse_args([]).live is False
    assert parser.parse_args(["--live"]).live is True


# --- which events are live (shared with the supervisor, stdlib only) ---------------------------------------------------


def event(eid: str, day: str, results: list[Result | None]) -> Event:
    return Event(id=eid, slug=eid, name=eid, date=day, status="completed" if any(results) else "upcoming",
                 fights=[Fight(id=f"{eid}-{i}", red="a", blue="b", result=r) for i, r in enumerate(results)])


WIN = Result(winner="a", outcome="win", method="KO/TKO")


@pytest.mark.parametrize("day,results,due", [
    ("2026-10-31", [None, None], True),                 # today, nothing in yet
    ("2026-10-31", [WIN, None], True),                  # today, part way
    ("2026-10-31", [WIN, WIN], False),                  # today, all in
    ("2026-10-30", [WIN, None], True),                  # yesterday: US cards end after midnight UTC
    ("2026-10-29", [WIN, None], False),                 # two days ago: the hourly runs and RESULTS_GRACE have it
    ("2026-11-01", [None, None], False),                # tomorrow
    ("2026-10-31", [Result(method="SUB"), WIN], True),  # a method without an outcome isn't a result yet
    ("2026-10-31", [], False),                          # no bouts
])
def test_due_events(tmp_path, day, results, due):
    store = Store(tmp_path)
    store.save_event(event("e1", day, results))
    store.save_index(Index(generated_at="2026-10-31T09:17:00Z", upcoming=["e1"], recent=[]))
    assert live.due_events(tmp_path, date(2026, 10, 31)) == (["e1"] if due else [])


def test_due_events_only_looks_at_indexed_events(tmp_path):
    store = Store(tmp_path)
    store.save_event(event("e1", "2026-10-31", [None]))
    store.save_event(event("e2", "2026-10-31", [WIN, None]))
    store.save_index(Index(generated_at="2026-10-31T09:17:00Z", upcoming=[], recent=["e2"]))
    assert live.due_events(tmp_path, date(2026, 10, 31)) == ["e2"]


def test_due_events_without_an_index_is_empty(tmp_path):
    assert live.due_events(tmp_path, date(2026, 10, 31)) == []


def test_the_live_window_sits_inside_the_results_grace():
    assert live.LIVE_DAYS < stage.RESULTS_GRACE.days


def test_live_is_stdlib_only():
    # the supervisor (PID 1) imports it
    imports = [line.split()[1] for line in Path(live.__file__).read_text().splitlines()
               if line.startswith(("import ", "from "))]
    assert all(not m.startswith(("fightcard", "pydantic", "bs4")) or m == "__future__" for m in imports), imports
