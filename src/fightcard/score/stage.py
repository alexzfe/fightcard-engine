"""`fightcard score`: finish tiers for every bout not yet frozen.

- A bout gets no tier (null, "—") when either fighter has no UFC bouts in the
  snapshot, or is stale: their record in fighters.json differs from the
  snapshot's (they've fought since the export). Stale fighters are listed in
  data/stale_fighters.json for the "Re-export finish model" issue.
- Once an event's date is today or earlier (UTC), its tiers are frozen: set
  `tier_frozen_at` once and never touch `finish_tier` again. A bout whose result
  is already in keeps its stored tier (its fighters' records moved because of
  that bout) and its fighters are not reported as stale.
- `index.model` comes from model/manifest.json.
- Each fighter's rating fields (`elo`, `elo_peak`, `p4p_rank`, the tags) come
  from model/fighter_rating.parquet rolled forward over results since the
  export and decayed to `--now` (see `fightcard.score.elo`). Without that file
  every fighter is unrated.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from fightcard.models import BestWin, Event, Fight, Fighter, ModelInfo
from fightcard.score.bundle import Bundle, load_bundle
from fightcard.score.elo import fighter_ratings
from fightcard.score.features import bout_context, bout_row
from fightcard.store import Store, write_json


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model-dir", type=Path, default=Path("model"), help="vendored model export")
    parser.add_argument("--now", default=None, help="ISO 8601 UTC time to act as (default: now)")


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


def _rating_fields(r: dict) -> dict:
    """`fighter_ratings` output as Fighter field values (model_copy doesn't validate, so build BestWin here)."""
    return r | {"best_win": None if r["best_win"] is None else BestWin(**r["best_win"])}


def run(args: argparse.Namespace) -> int:
    try:
        bundle = load_bundle(args.model_dir)
    except FileNotFoundError as e:
        print(f"score: {e}", file=sys.stderr)
        return 1
    store = Store(args.data)
    now = _now(args.now)
    fighters = store.load_fighters()
    rating = fighter_ratings(fighters, bundle.ratings, bundle.snapshot, bundle.manifest["as_of"], now.date())
    fighters = {fid: f.model_copy(update=_rating_fields(rating[fid])) for fid, f in fighters.items()}
    store.save_fighters(fighters)
    events, stale = score_events(list(store.load_events().values()), fighters, bundle, now)
    for ev in events:
        store.save_event(ev)
    stale_path = store.root / "stale_fighters.json"
    if stale:
        write_json(stale_path, stale)
    else:
        stale_path.unlink(missing_ok=True)
    index = store.load_index()
    if index is not None:
        index.model = ModelInfo(as_of=bundle.manifest["as_of"], version=bundle.manifest["version"])
        store.save_index(index)
    scored = sum(f.finish_tier is not None for ev in events for f in ev.fights)
    rated = sum(r["elo"] is not None for r in rating.values())
    print(f"score: {scored} bouts with a tier, {len(stale)} stale fighter entries, {rated} fighters with an Elo "
          f"(model {bundle.manifest['version']})", file=sys.stderr)
    return 0
