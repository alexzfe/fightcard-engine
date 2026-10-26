"""The displayed rating (docs/ELO.md Display), from the full-history pass (`fightcard.history.rating`).

Everyone rated is decayed to now, then shifted so the mean of established fighters (n ≥ 3, a bout in the last
730 days) is 1500. P4P ranks (top 15 men, top 10 women) go to fighters with ≥5 UFC bouts and a bout in the last
365 days. A fighter with no bout in scope (or no history at all) is unrated: every field null / false.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import pandas as pd

from fightcard.history.rating import PARAMS, START, decayed

_D = PARAMS["display"]
ESTABLISHED_N, ANCHOR_DAYS = _D["anchor_min_bouts"], _D["anchor_active_days"]
P4P_UFC_BOUTS, ACTIVE_DAYS = _D["p4p_min_ufc_bouts"], _D["active_days"]
P4P_SIZE = {"M": _D["p4p_men"], "F": _D["p4p_women"]}
PROVISIONAL_UFC_BOUTS = _D["provisional_below_ufc_bouts"]


def _unrated() -> dict:
    return {"elo": None, "elo_peak": None, "p4p_rank": None, "elo_provisional": False, "elo_inactive": False,
            "best_win": None}


def _value(r: pd.Series, column: str):
    """A row's value, or None when null."""
    v = r[column]
    return None if v is None or pd.isna(v) else v


def fighter_ratings(fighter_ids: Iterable[str], ratings: pd.DataFrame | None, now: date) -> dict[str, dict]:
    """The Fighter rating fields (`elo`, `elo_peak`, `p4p_rank`, `elo_provisional`, `elo_inactive`, `best_win`)
    for each of `fighter_ids`.

    `ratings` is `rating.history_ratings` (`rating.COLUMNS` indexed by fighter_id; None without a history).
    The anchor and the ranking cover every rated fighter, not just `fighter_ids`."""
    ids = list(fighter_ids)
    if ratings is None:
        return {fid: _unrated() for fid in ids}
    days = {fid: (now - date.fromisoformat(last)).days for fid, last in ratings["last_bout_date"].items()}
    current = {fid: decayed(float(r), days[fid]) for fid, r in ratings["rating"].items()}
    anchor = [current[fid] for fid, n in ratings["n_bouts"].items() if n >= ESTABLISHED_N and days[fid] <= ANCHOR_DAYS]
    shift = START - sum(anchor) / len(anchor) if anchor else 0.0

    rank: dict[str, int] = {}
    for gender, size in P4P_SIZE.items():
        pool = [fid for fid, r in ratings.iterrows()
                if r["gender"] == gender and r["n_ufc_bouts"] >= P4P_UFC_BOUTS and days[fid] <= ACTIVE_DAYS]
        pool.sort(key=lambda fid: (-current[fid], fid))
        rank |= {fid: i for i, fid in enumerate(pool[:size], start=1)}

    out = {}
    for fid in ids:
        if fid not in current:
            out[fid] = _unrated()
            continue
        r = ratings.loc[fid]
        peak, best = _value(r, "peak"), _value(r, "best_win_opponent_id")
        out[fid] = {"elo": round(current[fid] + shift),
                    "elo_peak": None if peak is None else round(peak + shift),
                    "p4p_rank": rank.get(fid),
                    "elo_provisional": bool(r["n_ufc_bouts"] < PROVISIONAL_UFC_BOUTS),
                    "elo_inactive": days[fid] > ACTIVE_DAYS,
                    "best_win": None if best is None else {
                        "opponent_id": best, "opponent": _value(r, "best_win_opponent") or best,
                        "elo": round(float(r["best_win_rating"]) + shift), "date": r["best_win_date"]}}
    return out
