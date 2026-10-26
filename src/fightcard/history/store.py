"""The bout-history store: one SQLite file (stdlib sqlite3).

Tables (keyed upserts, so re-ingesting the same pages changes nothing):
- `events`: one row per ufcstats event, with `is_ufc` (`is_ufc_event`) and `source` ("listing" when the event is
  in ufcstats' completed-events listing, else "history": UFC 1, DWCS, PRIDE, ... found by the crawler).
- `fights`: one row per bout in card order. `outcome` is "win" / "draw" / "nc", null until there's a result.
- `fighters`: name from the cards, bio and record from the fighter page when it was ingested.
- `pages`: the raw HTML each row came from (url, path under the HTML dir, sha256, fetched_at).
- `backfill_skips`: `fightcard scrape`'s bookkeeping, not history: events its backfill skipped (page gone, or a
  bout without an outcome), when it last tried and why; retried after a while. Not in `TABLES` (not dumped, and
  `score` doesn't need it).
Round stats are deferred.

The raw HTML stays the source of truth: a parser fix means re-ingesting, not re-crawling. `dump` writes each
table as sorted JSON lines for diffs. Callers own transactions (`with conn:`).
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from fightcard.models import Event, Fighter

TABLES = ("events", "fights", "fighters", "pages")
KEYS = {"events": "event_id", "fights": "fight_id", "fighters": "fighter_id", "pages": "url"}
MIN_CARD_BOUTS = 4  # a non-UFC card with fewer bouts with a result is out of the rating's scope ("sparse")
_UFC_NAME = re.compile(r"^UFC\b(?! BJJ)")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    date TEXT NOT NULL,
    location TEXT,
    status TEXT NOT NULL CHECK (status IN ('upcoming', 'completed')),
    is_ufc INTEGER NOT NULL CHECK (is_ufc IN (0, 1)),
    source TEXT NOT NULL CHECK (source IN ('listing', 'history'))
);
CREATE TABLE IF NOT EXISTS fights (
    fight_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events (event_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    red TEXT NOT NULL,
    blue TEXT NOT NULL,
    weight_class TEXT,
    title_fight INTEGER NOT NULL DEFAULT 0,
    outcome TEXT CHECK (outcome IN ('win', 'draw', 'nc')),
    winner TEXT,
    method TEXT,
    round INTEGER,
    time TEXT
);
CREATE INDEX IF NOT EXISTS fights_event ON fights (event_id);
CREATE TABLE IF NOT EXISTS fighters (
    fighter_id TEXT PRIMARY KEY,
    name TEXT,
    nickname TEXT,
    dob TEXT,
    height_in REAL,
    reach_in REAL,
    stance TEXT,
    record_w INTEGER,
    record_l INTEGER,
    record_d INTEGER,
    record_nc INTEGER,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS backfill_skips (
    event_id TEXT PRIMARY KEY,
    tried_at TEXT NOT NULL,
    reason TEXT NOT NULL
);
"""


def is_ufc_event(name: str, listed: bool) -> bool:
    """A UFC event is one in ufcstats' completed-events listing (which also holds the TUF finales, Noche UFC and
    "Ortiz vs Shamrock 3") or, among the history events, one named "UFC ..." (not "UFC BJJ"). Agrees with
    fight-auto-research's `event_source.csv` `is_ufc` on all 1,280 events."""
    return listed or bool(_UFC_NAME.match(name))


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_event(conn: sqlite3.Connection, event: Event, is_ufc: bool, source: str) -> None:
    """The event and its card. Bouts no longer on the card are removed."""
    conn.execute(
        "INSERT INTO events (event_id, name, date, location, status, is_ufc, source) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (event_id) DO UPDATE SET name = excluded.name, date = excluded.date, "
        "location = excluded.location, status = excluded.status, is_ufc = excluded.is_ufc, source = excluded.source",
        (event.id, event.name, event.date, event.location, event.status, int(is_ufc), source))
    conn.execute("DELETE FROM fights WHERE event_id = ? AND fight_id NOT IN (SELECT value FROM json_each(?))",
                 (event.id, json.dumps([f.id for f in event.fights])))
    for pos, f in enumerate(event.fights):
        r = f.result
        conn.execute(
            "INSERT INTO fights (fight_id, event_id, position, red, blue, weight_class, title_fight, outcome, winner, "
            "method, round, time) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (fight_id) DO UPDATE SET event_id = excluded.event_id, position = excluded.position, "
            "red = excluded.red, blue = excluded.blue, weight_class = excluded.weight_class, "
            "title_fight = excluded.title_fight, outcome = excluded.outcome, winner = excluded.winner, "
            "method = excluded.method, round = excluded.round, time = excluded.time",
            (f.id, event.id, pos, f.red, f.blue, f.weight_class, int(f.title_fight),
             r and r.outcome, r and r.winner, r and r.method, r and r.round, r and r.time))


def upsert_fighter(conn: sqlite3.Connection, fighter_id: str, name: str | None, page: Fighter | None = None,
                   fetched_at: str | None = None) -> None:
    """A name seen on a card (`page` None), or a fighter page. Fields a card doesn't know are kept, and so is a
    name from a fighter page (rows with a record): the page's spelling wins whichever order they come in."""
    rec = page.record if page else None
    conn.execute(
        "INSERT INTO fighters (fighter_id, name, nickname, dob, height_in, reach_in, stance, record_w, record_l, "
        "record_d, record_nc, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (fighter_id) DO UPDATE SET name = CASE WHEN excluded.record_w IS NULL AND record_w IS NOT NULL "
        "THEN name ELSE COALESCE(excluded.name, name) END, "
        "nickname = COALESCE(excluded.nickname, nickname), dob = COALESCE(excluded.dob, dob), "
        "height_in = COALESCE(excluded.height_in, height_in), reach_in = COALESCE(excluded.reach_in, reach_in), "
        "stance = COALESCE(excluded.stance, stance), record_w = COALESCE(excluded.record_w, record_w), "
        "record_l = COALESCE(excluded.record_l, record_l), record_d = COALESCE(excluded.record_d, record_d), "
        "record_nc = COALESCE(excluded.record_nc, record_nc), fetched_at = COALESCE(excluded.fetched_at, fetched_at)",
        (fighter_id, name or None, page and page.nickname, page and page.dob, page and page.height_in,
         page and page.reach_in, page and page.stance, rec and rec.w, rec and rec.l, rec and rec.d, rec and rec.nc,
         fetched_at))


def upsert_page(conn: sqlite3.Connection, url: str, path: str, sha256: str, fetched_at: str | None) -> None:
    conn.execute(
        "INSERT INTO pages (url, path, sha256, fetched_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (url) DO UPDATE SET path = excluded.path, sha256 = excluded.sha256, "
        "fetched_at = excluded.fetched_at",
        (url, path, sha256, fetched_at))


def dump(conn: sqlite3.Connection, out_dir: Path) -> list[Path]:
    """`<table>.jsonl` per table, rows sorted by key, keys sorted: byte-identical for identical stores."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for table in TABLES:
        cur = conn.execute(f"SELECT * FROM {table} ORDER BY {KEYS[table]}")  # fixed names, no input
        cols = [d[0] for d in cur.description]
        path = out_dir / f"{table}.jsonl"
        path.write_text("".join(json.dumps(dict(zip(cols, row)), sort_keys=True, ensure_ascii=False) + "\n"
                                for row in cur), encoding="utf-8")
        paths.append(path)
    return paths


def rating_bouts(conn: sqlite3.Connection, as_of: str | None = None) -> list[dict]:
    """The rating's bout records (`fightcard.history.rating`), by date then fight_id: every bout with a result,
    dated up to `as_of` (inclusive; None = all), on a UFC event or a card with >= MIN_CARD_BOUTS bouts with a
    result. fighter_a is the lower id; `result` is from fighter_a's side."""
    rows = conn.execute(
        "WITH card AS (SELECT event_id, COUNT(*) AS n FROM fights WHERE outcome IS NOT NULL GROUP BY event_id) "
        "SELECT e.date, f.fight_id, f.red, f.blue, f.outcome, f.winner, f.method, e.is_ufc, f.weight_class "
        "FROM fights f JOIN events e USING (event_id) JOIN card c USING (event_id) "
        "WHERE f.outcome IS NOT NULL AND e.date != '' AND (e.is_ufc = 1 OR c.n >= ?) AND (? IS NULL OR e.date <= ?) "
        "ORDER BY e.date, f.fight_id",
        (MIN_CARD_BOUTS, as_of, as_of))
    out = []
    for day, fight_id, red, blue, outcome, winner, method, is_ufc, weight_class in rows:
        a, b = sorted((red, blue))
        result = ("win" if winner == a else "loss") if outcome == "win" else outcome
        out.append({"date": day, "fight_id": fight_id, "fighter_a": a, "fighter_b": b, "result": result,
                    "method": method, "ufc": bool(is_ufc), "weight_class": weight_class})
    return out


def complete_events(conn: sqlite3.Connection, event_ids: list[str]) -> set[str]:
    """Those of `event_ids` the store has in full: a completed row with no bouts (all cancelled) or with a bout
    with an outcome. A completed row whose bouts have no outcome yet is not complete: its results aren't in."""
    return {eid for (eid,) in conn.execute(
        "SELECT e.event_id FROM events e WHERE e.status = 'completed' "
        "AND e.event_id IN (SELECT value FROM json_each(?)) "
        "AND (NOT EXISTS (SELECT 1 FROM fights f WHERE f.event_id = e.event_id) "
        "OR EXISTS (SELECT 1 FROM fights f WHERE f.event_id = e.event_id AND f.outcome IS NOT NULL))",
        (json.dumps(list(event_ids)),))}


def names(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT fighter_id, name FROM fighters WHERE name IS NOT NULL"))
