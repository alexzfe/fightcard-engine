"""`fightcard score`: finish tiers for every bout not yet frozen.

- A bout gets no tier (null, "—") when either fighter has no UFC bouts in the
  snapshot, or is stale: their record in fighters.json differs from the
  snapshot's (they've fought since the export). Stale fighters are listed in
  data/stale_fighters.json for the "Re-export finish model" issue.
- Once an event's date is today or earlier (UTC), its tiers are frozen: set
  `tier_frozen_at` once and never touch `finish_tier` again. A bout whose result
  is already in keeps its stored tier (its fighters' records moved because of
  that bout) and its fighters are not reported as stale.
- `data/rankings.json` is written beside them from the same pass (`fightcard.history.rankings.published`): the P4P
  lists and the active-fighter index as of `--now`'s date, for `/v1/rankings.json`. A P4P entry's `image` is
  `images/<id>.webp` when that headshot is in data/ (`images` runs first and works out the same P4P ids from the
  store as of the run's date, so a fighter new to the list has theirs in the same run). `--no-history` neither writes nor removes it.
- `index.model` is the export summary the how-it-works page quotes (`fightcard.score.summary`), plus
  `stale_fighters`: how many distinct fighters the staleness guard nulled this run.
- Each fighter's rating fields (`elo`, `elo_peak`, `p4p_rank`, the tags, the
  best win) come from the full-history pass over the bout-history store
  (`--history-db`, filled by `fightcard history ingest`), counting bouts up to
  `--now` and decayed to it (`fightcard.history.rating` / `.display`). The
  default store is `<--data>/../history/history.sqlite` (beside data/, not the
  cwd). A missing, empty, non-SQLite or never-ingested store is an error: exit
  1 before writing anything, so the last good generation stays live. So is a
  stale store: a completed event in data/ dated up to `--now` the store doesn't
  have in full (a completed row with no bouts, or with a bout with a result).
  An event scrape's backfill skipped (in the store's `backfill_skips`, when the
  table is there) is a WARNING naming it and the reason instead. Each run logs
  the store's newest completed event and last page fetch against `--now`.
  `--no-history` scores tiers only and leaves the rating fields as they are.
- Each completed fight's `elo` (`fightcard.history.timeline`, from the same pass): both fighters' ratings going in
  and the bout's own change, on today's display shift (as `Fighter.elo`, so they can move a point between runs), and
  their P4P ranks going into the card's day (as of the day before). Null for a fight with no result or one the pass
  doesn't have (after `--now`, unrated, another fight id). `--no-history` leaves it as it is.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fightcard.history.display import fighter_ratings, scale
from fightcard.history.rankings import divisions, published
from fightcard.history.rating import HistoryPass, history_pass
from fightcard.history.store import TABLES, complete_events
from fightcard.history.timeline import Timeline
from fightcard.models import BestWin, Event, Fight, FightElo, Fighter, ModelInfo
from fightcard.score.bundle import Bundle, load_bundle
from fightcard.score.features import bout_context, bout_row
from fightcard.score.summary import model_summary
from fightcard.store import Store, write_json


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model-dir", type=Path, default=Path("model"), help="vendored model export")
    parser.add_argument("--fallback-model-dir", type=Path, default=None,
                        help="the model to use instead when --model-dir is there but won't load (the service passes "
                             "its pulled model as --model-dir and the baked one here)")
    parser.add_argument("--now", default=None, help="ISO 8601 UTC time to act as (default: now)")
    ratings = parser.add_mutually_exclusive_group()
    ratings.add_argument("--history-db", type=Path, default=None,
                         help="bout-history store (fightcard history ingest); "
                              "default <--data>/../history/history.sqlite")
    ratings.add_argument("--no-history", action="store_true",
                         help="tiers only: leave every fighter's rating fields as they are")


def _record(row) -> tuple[int, int, int, int]:
    return int(row["record_w"]), int(row["record_l"]), int(row["record_d"]), int(row["record_nc"])


def _guard(bundle: Bundle, fighters: dict[str, Fighter], fid: str) -> str | None:
    """Why a fighter can't be scored: "no_history", "stale", or None when they can."""
    snap = bundle.snapshot
    if fid not in snap.index or int(snap.loc[fid, "n_ufc_bouts"]) == 0:
        return "no_history"
    f = fighters.get(fid)
    if f is None:
        return "no_history"  # no current record to check the snapshot against
    r = f.record
    return "stale" if (r.w, r.l, r.d, r.nc) != _record(snap.loc[fid]) else None


def score_events(events: list[Event], fighters: dict[str, Fighter], bundle: Bundle,
                 now: datetime) -> tuple[list[Event], list[dict]]:
    """Events with tiers (re)computed and frozen as due, plus the stale entries."""
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    today = now.date().isoformat()
    stale: dict[tuple[str, str], dict] = {}
    todo: list[tuple[Fight, dict]] = []
    out = []
    for ev in events:
        ev = ev.model_copy(deep=True)
        freeze = ev.date <= today
        for pos, fight in enumerate(ev.fights):
            if fight.tier_frozen_at is not None:
                continue
            if freeze:
                fight.tier_frozen_at = stamp
                if fight.result is not None:
                    continue
            why = {fid: _guard(bundle, fighters, fid) for fid in (fight.red, fight.blue)}
            for fid, w in why.items():
                if w == "stale":
                    stale[(ev.id, fid)] = {"event_id": ev.id, "fighter_id": fid, "name": fighters[fid].name}
            if any(why.values()):
                fight.finish_tier = None
            else:
                todo.append((fight, bout_row(bundle.snapshot, bundle.spec, fight.red, fight.blue, ev.date,
                                             bout_context(ev, fight, pos))))
        out.append(ev)
    for (fight, _), p in zip(todo, bundle.predict([row for _, row in todo])):
        fight.finish_tier = bundle.tier(float(p))
    return out, [stale[k] for k in sorted(stale)]


def _now(arg: str | None) -> datetime:
    if arg is None:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(arg.replace("Z", "+00:00")).astimezone(timezone.utc)


class HistoryUnavailable(Exception):
    """The history store can't be read; the message is the one line `score` prints."""


def _store_line(conn: sqlite3.Connection, db: Path, now: datetime) -> str:
    newest = conn.execute("SELECT MAX(date) FROM events WHERE status = 'completed'").fetchone()[0]
    fetched = conn.execute("SELECT MAX(fetched_at) FROM pages").fetchone()[0]
    return (f"history store {db}: newest completed event {newest or 'unknown'}, last page fetched "
            f"{fetched or 'unknown'} (now {now.strftime('%Y-%m-%dT%H:%M:%SZ')})")


def _missing_events(conn: sqlite3.Connection, events: list[Event], as_of: str) -> list[str]:
    """Completed events dated up to `as_of` the store doesn't have in full (`complete_events`), sorted."""
    want = sorted(ev.id for ev in events if ev.status == "completed" and ev.date <= as_of)
    have = complete_events(conn, want)
    return [eid for eid in want if eid not in have]


def _backfill_skips(conn: sqlite3.Connection, tables: set[str]) -> dict[str, str]:
    """scrape's `backfill_skips`: event id -> why its backfill gave up on it. Empty when the table isn't there (a
    store made by `history ingest` before it existed)."""
    if "backfill_skips" not in tables:
        return {}
    return dict(conn.execute("SELECT event_id, reason FROM backfill_skips"))


def _history_ratings(db: Path, now: datetime, events: list[Event]) -> HistoryPass:
    """The full-history pass up to `now`'s date (read-only: score never writes the store), after checking the
    store is usable and has every completed event in `events` up to then: (ratings, the rating's bouts, names, log)."""
    as_of = now.date().isoformat()
    if not db.is_file():
        raise HistoryUnavailable(f"no history store at {db}; run `fightcard history ingest`, "
                                 f"or pass --no-history for tiers only")
    unusable = HistoryUnavailable(f"history store {db} is unusable (not an ingested history store); "
                                  f"run `fightcard history ingest`")
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        try:
            tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not set(TABLES) <= tables or conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0:
                raise unusable
            print(f"score: {_store_line(conn, db, now)}", file=sys.stderr)
            skips = _backfill_skips(conn, tables)
            missing = []
            for eid in _missing_events(conn, events, as_of):
                if eid in skips:
                    print(f"score: WARNING: completed event {eid} is not in the history store (scrape's backfill "
                          f"skipped it: {skips[eid]}); the ratings leave it out", file=sys.stderr)
                else:
                    missing.append(eid)
            if missing:
                raise HistoryUnavailable(f"completed events with no bouts in the history store: {', '.join(missing)}; "
                                         f"run `fightcard history ingest`")
            return history_pass(conn, as_of)
        except sqlite3.DatabaseError as e:
            raise unusable from e


def _rating_fields(r: dict) -> dict:
    """`fighter_ratings` output as Fighter field values (model_copy doesn't validate, so build BestWin here)."""
    return r | {"best_win": None if r["best_win"] is None else BestWin(**r["best_win"])}


def fight_elo(fight: Fight, day: str, timeline: Timeline, shift: float) -> FightElo | None:
    """`Fight.elo`: each fighter's rating going into the bout and its own change on the display `shift` (the one
    Fighter.elo uses), each rounded on its own, and their P4P ranks going into `day`. None without a result, or when
    the pass doesn't have this fight between these two fighters."""
    sides = timeline.bout(fight.id) if fight.result is not None else None
    if sides is None or set(sides) != {fight.red, fight.blue}:
        return None
    rank = {e["id"]: e["rank"] for entries in timeline.p4p(day).values() for e in entries}
    red, blue = sides[fight.red], sides[fight.blue]
    return FightElo(red_pre=round(red.pre + shift), red_delta=round(red.delta),
                    blue_pre=round(blue.pre + shift), blue_delta=round(blue.delta),
                    red_p4p_rank=rank.get(fight.red), blue_p4p_rank=rank.get(fight.blue))


def run(args: argparse.Namespace) -> int:
    model_dir = args.model_dir
    try:
        try:
            bundle = load_bundle(model_dir)
        except Exception as e:  # noqa: BLE001 - lightgbm, pyarrow and json each raise their own
            if args.fallback_model_dir is None:
                raise
            print(f"score: ERROR can't load the model in {model_dir} ({type(e).__name__}: {e}); "
                  f"using {args.fallback_model_dir}", file=sys.stderr)
            model_dir = args.fallback_model_dir
            bundle = load_bundle(model_dir)
    except FileNotFoundError as e:
        print(f"score: {e}", file=sys.stderr)
        return 1
    print(f"score: model {bundle.manifest['version']} (as_of {bundle.manifest.get('as_of')}) from {model_dir}",
          file=sys.stderr)
    store = Store(args.data)
    now = _now(args.now)
    fighters = store.load_fighters()
    events = list(store.load_events().values())
    rated = "ratings left as they are (--no-history)"
    timeline = None
    if not args.no_history:
        db = args.history_db or args.data.resolve().parent / "history" / "history.sqlite"
        try:
            hp = _history_ratings(db, now, events)
        except HistoryUnavailable as e:
            print(f"score: {e}", file=sys.stderr)
            return 1
        history, bouts, who, _ = hp
        timeline, shift = Timeline.from_pass(hp), scale(history, now.date())[2]
        rating = fighter_ratings(fighters, history, now.date())
        fighters = {fid: f.model_copy(update=_rating_fields(rating[fid])) for fid, f in fighters.items()}
        store.save_fighters(fighters)
        headshots = {p.stem for p in (store.root / "images").glob("*.webp")}
        write_json(store.root / "rankings.json",
                   published(history, who, divisions(bouts), now.date(), headshots))
        rated = f"{sum(r['elo'] is not None for r in rating.values())} fighters with an Elo"
    events, stale = score_events(events, fighters, bundle, now)
    if timeline is not None:
        for ev in events:
            for fight in ev.fights:
                fight.elo = fight_elo(fight, ev.date, timeline, shift)
    for ev in events:
        store.save_event(ev)
    stale_path = store.root / "stale_fighters.json"
    if stale:
        write_json(stale_path, stale)
    else:
        stale_path.unlink(missing_ok=True)
    index = store.load_index()
    if index is not None:
        index.model = ModelInfo(**model_summary(bundle), stale_fighters=len({s["fighter_id"] for s in stale}))
        store.save_index(index)
    scored = sum(f.finish_tier is not None for ev in events for f in ev.fights)
    print(f"score: {scored} bouts with a tier, {len(stale)} stale fighter entries, {rated} "
          f"(model {bundle.manifest['version']})", file=sys.stderr)
    return 0
