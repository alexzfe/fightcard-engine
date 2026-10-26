"""The history store (SQLite): keyed upserts, one transaction per ingest, idempotent re-ingest, sorted dump,
the UFC-event rule, and the bout records the rating reads."""

from __future__ import annotations

import json
import sqlite3

import pytest

from fightcard.history import ingest as ingest_mod
from fightcard.history.ingest import ingest
from fightcard.history.store import (
    TABLES,
    connect,
    dump,
    is_ufc_event,
    names,
    rating_bouts,
    upsert_event,
    upsert_fighter,
)
from fightcard.models import Event, Fight, Fighter, Record, Result

from history_helpers import FIXTURES, cross_check, read_csv

HTML = FIXTURES / "html"
UFC219, NOCHE, TUF, DWCS, FURY, UFC1, UPCOMING = ("04076783ca83d6ae", "8fa2b06572365321", "31ceaf0e670c1578",
                                                 "e78f601035d677f2", "7d96bc577e5178b2", "6420efac0578988b",
                                                 "64be3dc99eaa42b6")
CYBORG = "634bb0de2eb043b4"


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "history.sqlite")
    yield conn
    conn.close()


@pytest.fixture
def seeded(db):
    ingest(db, HTML)
    return db


def count(conn, table: str, where: str = "1", *params) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]


def event(eid="e1", name="UFC 1", day="2020-01-01", fights=()) -> Event:
    return Event(id=eid, slug="x", name=name, date=day, status="completed" if fights else "upcoming",
                 fights=list(fights))


def fight(fid, red="a", blue="b", outcome="win", winner="a", method="KO/TKO", wc="Lightweight") -> Fight:
    result = None if outcome is None else Result(winner=winner, outcome=outcome, method=method, round=1, time="1:00")
    return Fight(id=fid, red=red, blue=blue, weight_class=wc, result=result)


# --- UFC event rule -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, listed, ufc", [
    ("UFC 219: Cyborg vs. Holm", True, True),
    ("The Ultimate Fighter: Team Hughes vs. Team Franklin Finale", True, True),  # listed, name doesn't say UFC
    ("Noche UFC: Grasso vs. Shevchenko 2", True, True),
    ("UFC 1: The Beginning", False, True),  # a history event (not in the listing) named UFC
    ("UFC BJJ: Road to the Title", False, False),
    ("UFCW 3", False, False),
    ("DWCS 7.3", False, False),
    ("Road To UFC 5.1", False, False),
    ("PRIDE 8", False, False),
])
def test_ufc_event_is_listed_or_named_ufc(name, listed, ufc):
    assert is_ufc_event(name, listed) is ufc


# --- schema and upserts ---------------------------------------------------------------------------------------

def test_connect_creates_the_tables(db):
    got = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert TABLES == ("events", "fights", "fighters", "pages")
    assert set(TABLES) <= got


def test_upsert_event_replaces_fields_and_card_by_key(db):
    with db:
        upsert_event(db, event(fights=[fight("f1"), fight("f2")]), is_ufc=True, source="listing")
    with db:  # ufcstats corrects a result and drops a bout
        upsert_event(db, event(name="UFC 1: The Beginning", fights=[fight("f1", outcome="nc", winner=None)]),
                     is_ufc=True, source="listing")
    assert count(db, "events") == 1 and count(db, "fights") == 1
    assert db.execute("SELECT name FROM events").fetchone()[0] == "UFC 1: The Beginning"
    assert db.execute("SELECT outcome, winner FROM fights WHERE fight_id = 'f1'").fetchone() == ("nc", None)


def test_fights_need_their_event(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO fights (fight_id, event_id, position, red, blue) VALUES ('f', 'nope', 0, 'a', 'b')")


def test_outcome_is_constrained(db):
    with db:
        upsert_event(db, event(), is_ufc=True, source="listing")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO fights (fight_id, event_id, position, red, blue, outcome) "
                   "VALUES ('f', 'e1', 0, 'a', 'b', 'loss')")


def test_fighter_upsert_keeps_bio_a_card_name_doesnt_know(db):
    page = Fighter(id="a", name="Alex A", nickname="Nick", record=Record(w=3, l=1), height_in=70.0, stance="Orthodox",
                   dob="1990-01-02")
    with db:
        upsert_fighter(db, "a", "Alex A", page, fetched_at="2026-10-26T00:00:00Z")
        upsert_fighter(db, "a", "Alex Card")
    row = db.execute("SELECT name, nickname, height_in, stance, dob, record_w, record_l, fetched_at FROM fighters"
                     ).fetchone()
    assert row == ("Alex Card", "Nick", 70.0, "Orthodox", "1990-01-02", 3, 1, "2026-10-26T00:00:00Z")


# --- ingest ---------------------------------------------------------------------------------------------------

def test_ingest_loads_every_page_of_the_fixture(seeded):
    assert count(seeded, "events") == 7
    assert count(seeded, "fights", "outcome IS NOT NULL") == 41  # research's CSV has 41 bouts for these events
    assert count(seeded, "fights", "event_id = ?", UPCOMING) > 0 == count(
        seeded, "fights", "event_id = ? AND outcome IS NOT NULL", UPCOMING)
    assert count(seeded, "pages") == 7 + 2 + 1
    assert count(seeded, "fighters") == len({f for r in seeded.execute("SELECT red, blue FROM fights") for f in r})


def test_ingest_marks_ufc_events_and_their_source(seeded):
    rows = {r[0]: r[1:] for r in seeded.execute("SELECT event_id, is_ufc, source FROM events")}
    assert rows[UFC219] == rows[NOCHE] == rows[TUF] == (1, "listing")
    assert rows[UFC1] == (1, "history")
    assert rows[DWCS] == rows[FURY] == (0, "history")


def test_ingest_matches_research_on_every_fixture_bout(seeded):
    assert cross_check(seeded, read_csv(FIXTURES / "fight_data.csv")) == {}


def test_ingest_agrees_with_research_is_ufc_on_every_fixture_event(seeded):
    ours = dict(seeded.execute("SELECT event_id, is_ufc FROM events"))
    want = {r["event_id"]: int(r["is_ufc"]) for r in read_csv(FIXTURES / "event_source.csv")}
    assert want == {e: ours[e] for e in want}


def test_ingest_keeps_draws_and_no_contests_apart(seeded):
    rows = seeded.execute("SELECT outcome, winner FROM fights WHERE event_id = ? AND outcome != 'win'",
                          (UFC219,)).fetchall()
    assert sorted(rows) == [("draw", None), ("nc", None)]


def test_fighter_pages_fill_bio_and_name(seeded):
    name, record_w, dob = seeded.execute(
        "SELECT name, record_w, dob FROM fighters WHERE fighter_id = ?", (CYBORG,)).fetchone()
    assert name == "Cris Cyborg" and record_w > 20 and dob


def test_pages_record_url_path_hash_and_fetch_time(seeded):
    url, sha, fetched = seeded.execute(
        "SELECT url, sha256, fetched_at FROM pages WHERE path = ?", (f"event-details/{UFC219}.html",)).fetchone()
    assert url == f"http://ufcstats.com/event-details/{UFC219}"
    assert len(sha) == 64 and fetched.endswith("Z")
    assert seeded.execute("SELECT url, fetched_at FROM pages WHERE path = ?",
                          (f"event-details/{UPCOMING}.html",)).fetchone() == (
        f"http://ufcstats.com/event-details/{UPCOMING}", None)  # no fetch-log line: time unknown
    assert count(seeded, "pages", "url = ?", f"http://ufcstats.com/fighter-details/{CYBORG}") == 1


def test_reingest_is_idempotent(seeded, tmp_path):
    first = dump(seeded, tmp_path / "a")
    ingest(seeded, HTML)
    second = dump(seeded, tmp_path / "b")
    assert [p.name for p in first] == [f"{t}.jsonl" for t in TABLES]
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]


def test_dump_is_sorted_json_lines(seeded, tmp_path):
    lines = dump(seeded, tmp_path)[1].read_text().splitlines()
    rows = [json.loads(line) for line in lines]
    assert [r["fight_id"] for r in rows] == sorted(r["fight_id"] for r in rows)
    assert all(line == json.dumps(r, sort_keys=True, ensure_ascii=False) for line, r in zip(lines, rows))


def test_a_failing_ingest_leaves_the_store_unchanged(seeded, tmp_path, monkeypatch):
    before = dump(seeded, tmp_path / "before")
    real = ingest_mod.parse_event_detail
    calls = []

    def fails_on_the_third_page(html, event_id):
        calls.append(event_id)
        if len(calls) == 3:
            raise ValueError("bad page")
        return real(html, event_id)

    monkeypatch.setattr(ingest_mod, "parse_event_detail", fails_on_the_third_page)
    with seeded:  # an earlier committed change the failed run must not undo
        seeded.execute("UPDATE events SET name = 'renamed' WHERE event_id = ?", (UFC219,))
    with pytest.raises(ValueError):
        ingest(seeded, HTML)
    after = dump(seeded, tmp_path / "after")
    assert seeded.execute("SELECT name FROM events WHERE event_id = ?", (UFC219,)).fetchone()[0] == "renamed"
    assert [p.read_text().count("\n") for p in after] == [p.read_text().count("\n") for p in before]


def test_ingest_without_the_completed_listing_refuses(tmp_path, db):
    (tmp_path / "event-details").mkdir()
    with pytest.raises(FileNotFoundError, match="events_completed"):
        ingest(db, tmp_path)


# --- bout records for the rating ------------------------------------------------------------------------------

def test_rating_bouts_are_research_records_in_scope_and_date_order(seeded):
    bouts = rating_bouts(seeded)
    event_of = dict(seeded.execute("SELECT fight_id, event_id FROM fights"))
    assert {event_of[b["fight_id"]] for b in bouts} == {UFC219, NOCHE, TUF, DWCS, UFC1}  # FURY: 1 bout, not UFC
    assert [(b["date"], b["fight_id"]) for b in bouts] == sorted((b["date"], b["fight_id"]) for b in bouts)
    assert all(b["fighter_a"] < b["fighter_b"] for b in bouts)
    assert set(bouts[0]) == {"date", "fight_id", "fighter_a", "fighter_b", "result", "method", "ufc", "weight_class"}
    assert {b["ufc"] for b in bouts if event_of[b["fight_id"]] == DWCS} == {False}
    assert {b["ufc"] for b in bouts if event_of[b["fight_id"]] != DWCS} == {True}


def test_rating_bouts_take_the_result_from_fighter_as_side(db):
    with db:
        upsert_event(db, event(fights=[fight("f1", red="b", blue="a", winner="b"), fight("f2", red="c", blue="d",
                                                                                       winner="c"),
                                       fight("f3", outcome="draw", winner=None, method="S-DEC"),
                                       fight("f4", outcome="nc", winner=None, method="Overturned"),
                                       fight("f5", outcome=None)]), is_ufc=True, source="listing")
    got = {b["fight_id"]: (b["fighter_a"], b["fighter_b"], b["result"], b["method"]) for b in rating_bouts(db)}
    assert got == {"f1": ("a", "b", "loss", "KO/TKO"), "f2": ("c", "d", "win", "KO/TKO"),
                   "f3": ("a", "b", "draw", "S-DEC"), "f4": ("a", "b", "nc", "Overturned")}


def test_a_non_ufc_card_needs_four_bouts_with_a_result(db):
    with db:
        upsert_event(db, event("e1", name="PRIDE 1", fights=[fight(f"f{i}") for i in range(3)] + [
            fight("f9", outcome=None)]), is_ufc=False, source="history")
        upsert_event(db, event("e2", name="PRIDE 2", fights=[fight(f"g{i}") for i in range(4)]), is_ufc=False,
                     source="history")
    assert {b["fight_id"][0] for b in rating_bouts(db)} == {"g"}


def test_rating_bouts_stop_at_as_of(db):
    with db:
        upsert_event(db, event("e1", day="2026-10-13", fights=[fight("f1")]), is_ufc=True, source="listing")
        upsert_event(db, event("e2", day="2026-10-17", fights=[fight("f2")]), is_ufc=True, source="listing")
    assert [b["fight_id"] for b in rating_bouts(db, as_of="2026-10-14")] == ["f1"]
    assert [b["fight_id"] for b in rating_bouts(db)] == ["f1", "f2"]


def test_names_prefer_the_fighter_page(seeded):
    assert names(seeded)[CYBORG] == "Cris Cyborg"
