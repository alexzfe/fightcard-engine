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


def scale(ratings: pd.DataFrame, now: date) -> tuple[dict[str, int], dict[str, float], float]:
    """(days since each fighter's last bout, their raw rating decayed to `now`, the anchor shift): the displayed
    rating is `current + shift`, a peak or best win `raw + shift`."""
    days = {fid: (now - date.fromisoformat(last)).days for fid, last in ratings["last_bout_date"].items()}
    current = {fid: decayed(float(r), days[fid]) for fid, r in ratings["rating"].items()}
    anchor = [current[fid] for fid, n in ratings["n_bouts"].items() if n >= ESTABLISHED_N and days[fid] <= ANCHOR_DAYS]
    return days, current, START - sum(anchor) / len(anchor) if anchor else 0.0


def rankable(ratings: pd.DataFrame, days: dict[str, int], current: dict[str, float]) -> list[str]:
    """Fighters eligible for a ranking (≥ P4P_UFC_BOUTS UFC bouts, a bout in ACTIVE_DAYS), best first (ties by id)."""
    pool = [fid for fid, n in ratings["n_ufc_bouts"].items() if n >= P4P_UFC_BOUTS and days[fid] <= ACTIVE_DAYS]
    return sorted(pool, key=lambda fid: (-current[fid], fid))


def p4p_ranks(ratings: pd.DataFrame, days: dict[str, int], current: dict[str, float]) -> dict[str, int]:
    """P4P rank by fighter: the top P4P_SIZE of each gender among `rankable`."""
    rank: dict[str, int] = {}
    pool = rankable(ratings, days, current)
    for gender, size in P4P_SIZE.items():
        ranked = [fid for fid in pool if ratings.at[fid, "gender"] == gender][:size]
        rank |= {fid: i for i, fid in enumerate(ranked, start=1)}
    return rank


def fighter_ratings(fighter_ids: Iterable[str], ratings: pd.DataFrame | None, now: date) -> dict[str, dict]:
    """The Fighter rating fields (`elo`, `elo_peak`, `p4p_rank`, `elo_provisional`, `elo_inactive`, `best_win`)
    for each of `fighter_ids`.

    `ratings` is `rating.history_ratings` (`rating.COLUMNS` indexed by fighter_id; None without a history).
    The anchor and the ranking cover every rated fighter, not just `fighter_ids`."""
    ids = list(fighter_ids)
    if ratings is None:
        return {fid: _unrated() for fid in ids}
    days, current, shift = scale(ratings, now)
    rank = p4p_ranks(ratings, days, current)

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
