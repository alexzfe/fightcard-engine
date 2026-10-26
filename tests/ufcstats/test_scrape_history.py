"""`fightcard scrape` keeps the history store current: the pages it fetched go into the store in one transaction,
through the same parse + upsert path as `history ingest`."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from test_scrape_stage import BASE, COMPLETED_ID, NOW, UPCOMING_CARD, UPCOMING_IDS, FakeClient, read_fixture, site_pages

from fightcard.cli import main
from fightcard.history import ingest as ingest_mod
from fightcard.history import store as history_store
from fightcard.history.ingest import ingest
from fightcard.history.rating import history_ratings
from fightcard.history.store import connect, dump
from fightcard.models import Fighter
from fightcard.score import stage as score_stage
from fightcard.store import Store
from fightcard.ufcstats import stage
from fightcard.ufcstats.client import HttpError
from fightcard.ufcstats.parsers import parse_event_detail

HISTORY_HTML = Path(__file__).resolve().parent.parent / "fixtures" / "history" / "html"
MODEL_DIR = Path(__file__).resolve().parents[2] / "model"
CARD_URL = f"{BASE}/event-details/{COMPLETED_ID}"
CARD = parse_event_detail(read_fixture("event_detail_completed.html"), COMPLETED_ID).event
WINNERS = sorted({f.result.winner for f in CARD.fights if f.result and f.result.winner})


def as_upcoming(html: str) -> str:
    """The completed card as it looked before the results: no W/L flag, method, round or time."""
    soup = BeautifulSoup(html, "lxml")
    for row in soup.select("tbody.b-fight-details__table-body tr.b-fight-details__table-row"):
        cols = row.find_all("td")
        for i in (0, 7, 8, 9):
            cols[i].clear()
    return str(soup)


def client(card: str | None = None, fail: str | None = None) -> FakeClient:
    pages = site_pages()
    if card is not None:
        pages[CARD_URL] = card
    if fail is not None:
        pages.pop(fail)
    return FakeClient(pages, fighter_page=read_fixture("fighter.html"))


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "history" / "history.sqlite"


@pytest.fixture
def db(db_path):
    """A store seeded from the committed history fixture (UFC 219, Noche UFC, ...), as `history ingest` makes it."""
    conn = connect(db_path)
    ingest(conn, HISTORY_HTML)
    yield conn
    conn.close()


def run(tmp_path, conn, c, now=NOW, recent=1):
    return stage.scrape(Store(tmp_path / "data"), c, now=now, recent=recent, history=conn)


def card_rows(conn, event_id=COMPLETED_ID):
    return conn.execute("SELECT fight_id, outcome, winner, method FROM fights WHERE event_id = ? ORDER BY position",
                        (event_id,)).fetchall()


def dump_bytes(conn, out: Path) -> list[bytes]:
    return [p.read_bytes() for p in dump(conn, out)]


def score(tmp_path, db_path: Path, now: str) -> dict:
    assert main(["--data", str(tmp_path / "data"), "score", "--model-dir", str(MODEL_DIR),
                 "--history-db", str(db_path), "--now", now]) == 0
    return json.loads((tmp_path / "data" / "fighters.json").read_text())


# --- acceptance -------------------------------------------------------------------------------------------------


def test_a_card_going_upcoming_to_completed_updates_the_store_and_score_rates_the_winners(tmp_path, db, db_path):
    assert run(tmp_path, db, client(card=as_upcoming(read_fixture("event_detail_completed.html")))) == 0
    assert db.execute("SELECT status FROM events WHERE event_id = ?", (COMPLETED_ID,)).fetchone() == ("upcoming",)
    assert {outcome for _, outcome, _, _ in card_rows(db)} == {None}
    before = score(tmp_path, db_path, "2026-10-16T00:00:00Z")
    n_before = history_ratings(db, "2026-10-16")["n_bouts"].reindex(WINNERS, fill_value=0)

    assert run(tmp_path, db, client(), now=NOW + timedelta(days=1)) == 0
    assert db.execute("SELECT status FROM events WHERE event_id = ?", (COMPLETED_ID,)).fetchone() == ("completed",)
    assert card_rows(db) == [(f.id, f.result.outcome, f.result.winner, f.result.method) for f in CARD.fights]

    after = score(tmp_path, db_path, "2026-10-16T00:00:00Z")  # no re-ingest in between
    ratings = history_ratings(db, "2026-10-16").loc[WINNERS]
    assert (ratings["n_bouts"] == n_before + 1).all()  # the new bout counts
    assert set(ratings["last_bout_date"]) == {CARD.date}
    assert all(after[w]["elo"] is not None for w in WINNERS)
    assert any(before[w]["elo"] is None for w in WINNERS)  # most had no bout in the store before
    assert all(after[w]["elo"] != before[w]["elo"] for w in WINNERS)


def test_rerunning_on_the_same_pages_changes_nothing(tmp_path, db):
    assert run(tmp_path, db, client()) == 0
    first = dump_bytes(db, tmp_path / "a")
    assert run(tmp_path, db, client()) == 0
    assert dump_bytes(db, tmp_path / "b") == first


def test_an_event_page_failing_mid_run_leaves_the_store_as_it_was(tmp_path, db):
    before = dump_bytes(db, tmp_path / "before")
    assert run(tmp_path, db, client(fail=CARD_URL)) == 1
    assert dump_bytes(db, tmp_path / "after") == before
    assert not (tmp_path / "data" / "index.json").exists()


def test_a_store_write_failing_mid_run_rolls_back_and_writes_no_data(tmp_path, db, monkeypatch, capsys):
    before = dump_bytes(db, tmp_path / "before")
    real, calls = ingest_mod.upsert_fighter, []

    def fails_on_the_fifth(*args, **kwargs):
        calls.append(args[1])
        if len(calls) == 5:
            raise sqlite3.OperationalError("disk I/O error")
        return real(*args, **kwargs)

    monkeypatch.setattr(ingest_mod, "upsert_fighter", fails_on_the_fifth)
    assert run(tmp_path, db, client()) == 1
    assert len(calls) == 5
    assert dump_bytes(db, tmp_path / "after") == before
    assert not (tmp_path / "data" / "index.json").exists()
    assert "nothing written" in capsys.readouterr().err


# --- one parser path ----------------------------------------------------------------------------------------------


def save_pages(c: FakeClient, cache: Path) -> None:
    """The pages a run fetched, laid out as a `history ingest` cache."""
    for url in dict.fromkeys(c.urls):  # c.get below appends to c.urls
        if url == stage.COMPLETED_URL:
            rel = "listings/events_completed.html"
        elif "/event-details/" in url or "/fighter-details/" in url:
            kind, page_id = url.rsplit("/", 2)[-2:]
            rel = f"{kind}/{page_id}.html"
        else:
            continue
        path = cache / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(c.get(url), encoding="utf-8")


def rows(conn, table: str, drop: str | None = None) -> list[dict]:
    cur = conn.execute(f"SELECT * FROM {table} ORDER BY {history_store.KEYS[table]}")
    cols = [d[0] for d in cur.description]
    return [{k: v for k, v in zip(cols, r) if k != drop} for r in cur]


def test_the_store_is_what_history_ingest_makes_of_the_same_pages(tmp_path, db):
    c = client()
    assert run(tmp_path, db, c) == 0
    cache = tmp_path / "cache"
    save_pages(c, cache)
    with closing(connect(tmp_path / "other.sqlite")) as other:
        ingest(other, HISTORY_HTML)
        ingest(other, cache)
        for table in ("events", "fights"):
            assert rows(db, table) == rows(other, table)
        assert rows(db, "fighters", drop="fetched_at") == rows(other, "fighters", drop="fetched_at")
        assert rows(db, "pages", drop="fetched_at") == rows(other, "pages", drop="fetched_at")


def test_fetched_pages_are_recorded_with_this_run_s_time(tmp_path, db):
    assert run(tmp_path, db, client()) == 0
    stamp = stage.iso(NOW)
    assert db.execute("SELECT fetched_at FROM pages WHERE url = ?", (CARD_URL,)).fetchone() == (stamp,)
    fighter = CARD.fights[0].red
    assert db.execute("SELECT fetched_at FROM fighters WHERE fighter_id = ?", (fighter,)).fetchone() == (stamp,)


# --- the completed listing ------------------------------------------------------------------------------------------


def test_the_full_completed_listing_is_refreshed_even_with_recent_zero(tmp_path, db):
    c = client()
    assert run(tmp_path, db, c, recent=0) == 0
    assert stage.COMPLETED_URL == f"{BASE}/statistics/events/completed?page=all"  # what `history ingest` reads
    assert stage.COMPLETED_URL in c.urls
    assert db.execute("SELECT path, fetched_at FROM pages WHERE url = ?", (stage.COMPLETED_URL,)).fetchone() == (
        "listings/events_completed.html", stage.iso(NOW))


def test_without_a_store_recent_zero_still_skips_the_listing(tmp_path):
    c = client()
    assert stage.scrape(Store(tmp_path), c, now=NOW, recent=0) == 0
    assert stage.COMPLETED_URL not in c.urls


def test_a_listed_event_is_marked_ufc_from_the_listing(tmp_path, db):
    assert run(tmp_path, db, client()) == 0
    assert db.execute("SELECT is_ufc, source FROM events WHERE event_id = ?", (COMPLETED_ID,)).fetchone() == (
        1, "listing")


# --- completed events the store lacks -----------------------------------------------------------------------------


def test_a_completed_event_in_data_the_store_lacks_is_fetched_into_it(tmp_path, db):
    Store(tmp_path / "data").save_event(CARD)  # written by an earlier scrape, before the store existed
    c = client()
    assert run(tmp_path, db, c, recent=0) == 0
    assert CARD_URL in c.urls
    assert len(card_rows(db)) == len(CARD.fights)


def test_a_completed_event_the_store_already_has_is_not_refetched(tmp_path, db):
    Store(tmp_path / "data").save_event(CARD)
    assert run(tmp_path, db, client()) == 0
    c = client()
    assert run(tmp_path, db, c, recent=0) == 0
    assert CARD_URL not in c.urls


# --- CLI ------------------------------------------------------------------------------------------------------------


def cli(tmp_path, *extra) -> int:
    return main(["--data", str(tmp_path / "data"), "scrape", "--recent", "1", *extra])


@pytest.fixture
def fake_site(monkeypatch):
    c = client()
    monkeypatch.setattr(stage, "make_client", lambda cookie_file: c)
    monkeypatch.setattr(stage, "utcnow", lambda: NOW)
    return c


def test_cli_updates_the_store_given_by_history_db(tmp_path, db, db_path, fake_site):
    assert cli(tmp_path, "--history-db", str(db_path)) == 0
    with closing(sqlite3.connect(db_path)) as conn:
        assert len(card_rows(conn)) == len(CARD.fights)


def test_cli_without_a_store_fails_before_fetching_or_writing(tmp_path, fake_site, capsys):
    missing = tmp_path / "nope" / "history.sqlite"
    assert cli(tmp_path, "--history-db", str(missing)) == 1
    assert fake_site.urls == []
    assert not missing.exists() and not (tmp_path / "data").exists()
    err = capsys.readouterr().err
    assert "no history store" in err and "history ingest" in err and "--no-history" in err


def test_cli_no_history_scrapes_without_a_store(tmp_path, fake_site):
    assert cli(tmp_path, "--no-history") == 0
    assert (tmp_path / "data" / "index.json").exists()
    assert not (tmp_path / "history").exists()


def test_cli_no_history_and_history_db_are_exclusive(tmp_path, fake_site):
    with pytest.raises(SystemExit) as exc:
        cli(tmp_path, "--history-db", str(tmp_path / "x.sqlite"), "--no-history")
    assert exc.value.code == 2


def test_cli_default_store_sits_beside_data_like_score_s(tmp_path, db, fake_site, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # not the cwd: <--data>/../history/history.sqlite
    assert cli(tmp_path) == 0
    assert len(card_rows(db)) == len(CARD.fights)
    assert not (elsewhere / "history").exists()


def test_cli_flags_match_score_s():
    scrape_parser, score_parser = argparse.ArgumentParser(), argparse.ArgumentParser()
    stage.add_arguments(scrape_parser)
    score_stage.add_arguments(score_parser)
    ours, theirs = scrape_parser.parse_args([]), score_parser.parse_args([])
    assert (ours.history_db, ours.no_history) == (theirs.history_db, theirs.no_history) == (None, False)


# --- a result without an outcome --------------------------------------------------------------------------------


def without_flags(html: str) -> str:
    """The completed card with the W/L flags gone but methods kept: results the rating would silently drop."""
    soup = BeautifulSoup(html, "lxml")
    for row in soup.select("tbody.b-fight-details__table-body tr.b-fight-details__table-row"):
        row.find_all("td")[0].clear()
    return str(soup)


def test_a_completed_bout_with_a_method_but_no_outcome_fails_and_writes_nothing(tmp_path, db, capsys):
    before = dump_bytes(db, tmp_path / "before")
    assert run(tmp_path, db, client(card=without_flags(read_fixture("event_detail_completed.html")))) == 1
    assert dump_bytes(db, tmp_path / "after") == before
    assert not (tmp_path / "data" / "index.json").exists()
    err = capsys.readouterr().err
    assert "nothing written" in err and CARD.fights[0].id in err


# --- all or nothing: the store commits first, then data/ ------------------------------------------------------------


class CommitFails:
    """A store connection whose COMMIT fails, as with another writer holding the lock past the busy timeout."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def commit(self):
        raise sqlite3.OperationalError("database is locked")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type is None:
            self.commit()
        else:
            self.conn.rollback()


def test_a_store_commit_failing_leaves_data_untouched(tmp_path, db, capsys):
    before = dump_bytes(db, tmp_path / "before")
    assert run(tmp_path, CommitFails(db), client()) == 1
    assert not (tmp_path / "data").exists()
    assert dump_bytes(db, tmp_path / "after") == before
    assert "nothing written" in capsys.readouterr().err


def test_the_store_write_lock_is_taken_with_a_busy_timeout(tmp_path, db, db_path, monkeypatch, capsys):
    """Another writer holds the store: the run waits `BUSY_TIMEOUT_MS`, then fails before writing anything."""
    monkeypatch.setattr(stage, "BUSY_TIMEOUT_MS", 50)
    before = dump_bytes(db, tmp_path / "before")
    with closing(sqlite3.connect(db_path, isolation_level=None)) as other:
        other.execute("BEGIN IMMEDIATE")
        assert run(tmp_path, db, client()) == 1
        assert db.execute("PRAGMA busy_timeout").fetchone() == (50,)
        other.execute("ROLLBACK")
    assert not (tmp_path / "data").exists()
    assert dump_bytes(db, tmp_path / "after") == before
    err = capsys.readouterr().err
    assert "nothing written" in err and "locked" in err


def test_the_default_busy_timeout_is_thirty_seconds():
    assert stage.BUSY_TIMEOUT_MS == 30_000


def seeded(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    ingest(conn, HISTORY_HTML)
    return conn


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_a_data_write_failing_after_the_commit_says_so_and_the_next_run_converges(tmp_path, db, monkeypatch,
                                                                                  capsys):
    real, saved = Store.save_event, []

    def fails_on_the_second(self, event):
        saved.append(event.id)
        if len(saved) == 2:
            raise OSError(28, "No space left on device")
        return real(self, event)

    monkeypatch.setattr(Store, "save_event", fails_on_the_second)
    assert run(tmp_path, db, client()) == 1
    err = capsys.readouterr().err
    assert "store updated" in err and "data/ may be partially written" in err and "next run rewrites it" in err
    assert "nothing written" not in err
    assert len(card_rows(db)) == len(CARD.fights)  # committed before data/
    assert not (tmp_path / "data" / "index.json").exists()

    monkeypatch.setattr(Store, "save_event", real)
    assert run(tmp_path, db, client()) == 0
    with closing(seeded(tmp_path / "clean" / "history.sqlite")) as clean:
        assert run(tmp_path / "clean", clean, client()) == 0
        assert dump_bytes(db, tmp_path / "a") == dump_bytes(clean, tmp_path / "b")
    assert tree(tmp_path / "data") == tree(tmp_path / "clean" / "data")


def test_without_a_store_a_data_write_failing_exits_1_without_a_traceback(tmp_path, monkeypatch, capsys):
    def fails(self, fighters):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Store, "save_fighters", fails)
    assert stage.scrape(Store(tmp_path / "data"), client(), now=NOW) == 1
    err = capsys.readouterr().err
    assert "data/ may be partially written" in err and "next run rewrites it" in err


# --- backfill: completed events in data/ the store lacks ------------------------------------------------------------

OLD_ID = "0123456789abcdef"  # a completed event in data/, not in this run's cards
OLD_URL = f"{BASE}/event-details/{OLD_ID}"
OLD = CARD.model_copy(update={"id": OLD_ID, "date": "2019-03-02"})


def test_a_backfill_page_that_404s_is_skipped_with_a_warning(tmp_path, db, caplog):
    Store(tmp_path / "data").save_event(OLD)
    before = (tmp_path / "data" / "events" / f"{OLD_ID}.json").read_bytes()
    c = client()  # no page at OLD_URL: 404
    assert run(tmp_path, db, c) == 0
    assert OLD_URL in c.urls
    assert OLD_ID in caplog.text and "skipping" in caplog.text
    assert db.execute("SELECT COUNT(*) FROM events WHERE event_id = ?", (OLD_ID,)).fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM pages WHERE url = ?", (OLD_URL,)).fetchone() == (0,)
    assert (tmp_path / "data" / "events" / f"{OLD_ID}.json").read_bytes() == before
    assert len(card_rows(db)) == len(CARD.fights)  # the rest of the run went in


def test_a_backfill_page_without_outcomes_is_skipped_with_a_warning(tmp_path, db, caplog):
    Store(tmp_path / "data").save_event(OLD)
    before = (tmp_path / "data" / "events" / f"{OLD_ID}.json").read_bytes()
    c = client()
    c.pages[OLD_URL] = without_flags(read_fixture("event_detail_completed.html"))
    assert run(tmp_path, db, c) == 0
    assert OLD_ID in caplog.text and "skipping" in caplog.text and CARD.fights[0].id in caplog.text
    assert db.execute("SELECT COUNT(*) FROM events WHERE event_id = ?", (OLD_ID,)).fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM pages WHERE url = ?", (OLD_URL,)).fetchone() == (0,)
    assert (tmp_path / "data" / "events" / f"{OLD_ID}.json").read_bytes() == before
    assert card_rows(db) == [(f.id, f.result.outcome, f.result.winner, f.result.method) for f in CARD.fights]


def test_a_current_card_that_404s_still_fails_the_run(tmp_path, db):
    """The --recent card: what data/'s index shows next, so it fails loudly (also with a backfill pending)."""
    Store(tmp_path / "data").save_event(OLD)
    c = client(fail=CARD_URL)
    c.pages[OLD_URL] = read_fixture("event_detail_completed.html")
    assert run(tmp_path, db, c) == 1


def test_a_past_upcoming_card_without_outcomes_still_fails_the_run(tmp_path, db, capsys):
    """An upcoming card in data/ whose date passed: its results are current, not backfill."""
    Store(tmp_path / "data").save_event(CARD.model_copy(update={"status": "upcoming"}))
    assert run(tmp_path, db, client(card=without_flags(read_fixture("event_detail_completed.html"))),
               recent=0) == 1
    assert "nothing written" in capsys.readouterr().err


def backlog(tmp_path, n: int) -> tuple[list[str], FakeClient]:
    """n completed events in data/ the store lacks (oldest first), and a site with their pages: the completed
    card under its own fight ids."""
    c = client()
    ids = [f"{i:016x}" for i in range(1, n + 1)]
    for i, eid in enumerate(ids):
        html = read_fixture("event_detail_completed.html")
        for f in CARD.fights:
            html = html.replace(f.id, eid[-4:] + f.id[4:])
        c.pages[f"{BASE}/event-details/{eid}"] = html
        Store(tmp_path / "data").save_event(
            parse_event_detail(html, eid).event.model_copy(update={"date": f"2010-01-{i + 1:02d}"}))
    return ids, c


def backfilled(c: FakeClient, ids: list[str]) -> list[str]:
    return [eid for eid in ids if f"{BASE}/event-details/{eid}" in c.urls]


def test_backfill_refetches_are_capped_per_run_and_the_rest_come_next_run(tmp_path, db, caplog):
    caplog.set_level(logging.INFO)
    assert stage.BACKFILL_PER_RUN == 20
    ids, c = backlog(tmp_path, stage.BACKFILL_PER_RUN + 5)
    assert run(tmp_path, db, c) == 0
    assert backfilled(c, ids) == ids[: stage.BACKFILL_PER_RUN]
    assert "5" in caplog.text and "later" in caplog.text
    c.urls.clear()
    assert run(tmp_path, db, c) == 0
    assert backfilled(c, ids) == ids[stage.BACKFILL_PER_RUN:]


def test_backfill_cards_fetch_only_fighters_missing_from_data(tmp_path, db):
    """A backfilled card's fighters are already in fighters.json; stale ones there aren't refetched for it."""
    data = Store(tmp_path / "data")
    data.save_event(OLD)
    missing, *known = sorted({fid for f in OLD.fights for fid in (f.red, f.blue)})
    data.save_fighters({fid: Fighter(id=fid, name=fid, details_fetched_at="2020-01-01T00:00:00Z") for fid in known})
    c = client()
    c.pages[OLD_URL] = read_fixture("event_detail_completed.html")
    assert run(tmp_path, db, c, recent=0) == 0  # only the upcoming cards are current
    assert OLD_URL in c.urls
    upcoming = {fid for f in UPCOMING_CARD.event.fights for fid in (f.red, f.blue)}
    fetched = {u.rsplit("/", 1)[1] for u in c.urls if "/fighter-details/" in u}
    assert missing in fetched
    assert not (fetched & set(known)) - upcoming


# --- upcoming events with no bouts yet ------------------------------------------------------------------------------


def test_an_upcoming_event_skipped_for_0_fights_stays_out_of_the_store_as_out_of_data(tmp_path, db):
    far = UPCOMING_IDS[6]
    c = FakeClient(site_pages(empty=[far]), fighter_page=read_fixture("fighter.html"))
    assert run(tmp_path, db, c) == 0
    assert f"{BASE}/event-details/{far}" in c.urls
    assert not (tmp_path / "data" / "events" / f"{far}.json").exists()
    assert db.execute("SELECT COUNT(*) FROM events WHERE event_id = ?", (far,)).fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM pages WHERE url = ?", (f"{BASE}/event-details/{far}",)).fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM events WHERE event_id = ?", (UPCOMING_IDS[0],)).fetchone() == (1,)


def test_a_backfill_page_failing_with_another_http_error_still_fails_the_run(tmp_path, db):
    """Only a 404 (the page is gone) is skipped; a 5xx may pass by the next run."""
    Store(tmp_path / "data").save_event(OLD)
    c = client()
    real = c.get

    def get(url):
        if url == OLD_URL:
            c.urls.append(url)
            raise HttpError(url, 503)
        return real(url)

    c.get = get
    assert run(tmp_path, db, c) == 1
    assert not (tmp_path / "data" / "index.json").exists()
