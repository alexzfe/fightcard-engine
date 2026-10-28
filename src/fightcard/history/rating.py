"""Fighter rating (Elo): the single implementation, a chronological pass over the history store's bouts.

Ported from fight-auto-research `main:export/rating.py` (d179cf2 era), which computed it for the export until
then; spec and contract in docs/ELO.md. `history_pass(conn, as_of)` feeds `rate` from `fightcard.history.store`
(`rating_bouts`, `names`); `fightcard.history.display` turns its rows into the displayed numbers and
`fightcard.history.timeline` its per-bout log into ratings as of any date.

Rules (raw scale; re-anchoring, P4P rank and tags are `fightcard.history.display`'s, `PARAMS["display"]`):
  - scope: every bout with a result on a UFC event, or on another card with >= 4 bouts with a result
    ("full_card"; `store.rating_bouts`), the research build's default scope
  - every debutant starts at 1500; one rating per fighter, no weight-class handicap
  - E = 1 / (1 + 10^((Rb - Ra) / 400)); each side's K = 275 / (n + 5)^0.4, n = its prior bouts in scope
    (including NC and draws), no floor
  - W: win 1, draw 0.5, loss 0; split-decision win 0.6, majority-decision win 0.8 (the loser gets 1 - W);
    no update for a no contest or for CNC / Overturned whatever the recorded outcome; the method of finish
    adds nothing (KO, SUB, U-DEC, DQ all give 1)
  - decay is continuous and lazy: before a fighter's next bout R = 1500 + (R - 1500) * 0.9^(days / 365.25),
    days since the last bout in scope (an NC counts); the stored rating is the undecayed post-bout value
  - same day: decay everyone on the day to the day, compute every bout's change from those pre-day ratings,
    apply them all, then advance n and the last-bout date (an early-UFC tournament fighter's bouts all use
    the same pre-day rating and the same K)
  - peak: highest post-bout rating at a bout by which the fighter has >= 5 UFC bouts (counting that bout;
    a later non-UFC bout counts too, as in the backtest's lists), undecayed; null until then
  - gender: "F" with any Women's weight class, else "M" with any other named division, else null
  - best win: among the fighter's wins (split / majority included; draws, NC, CNC, Overturned excluded), the
    opponent with the highest pre-bout rating (decayed to that day); on a tie the most recent bout (later
    date, then later fight_id). Name from the store's fighters (fighter page, else the card); null without a win

It reproduces the backtest engine `scripts/elo_backtest/engine3.py` (`ksched=("tennis", 275, 0)`,
`split=("partial", 0.6, 0.8)`, `finish=("none",)`, `layoff=("day1", 0.1)`, `weight=("single",)`,
`debut=("const", 1500)`) except that engine3 scores CNC / Overturned bouts recorded as draws as 0.5.

Bout records (`rate`, `store.rating_bouts`, the golden cases): dicts with
  date "YYYY-MM-DD", fight_id str, fighter_a str, fighter_b str,
  result "win" | "loss" | "draw" | "nc" (fighter_a's side),
  method ufcstats fighter-page code: "KO/TKO" | "SUB" | "U-DEC" | "S-DEC" | "M-DEC" | "DQ" | "CNC" |
         "Overturned" | "Other" | null (null and anything unlisted score like "KO/TKO"),
  ufc bool (a UFC event), weight_class str | null.

`rating_cases.json` (`golden_cases`; pinned in tests/score/fixtures/rating_cases.json):
  {"format_version": 1, "params": PARAMS, "cases": [case, ...]}
  case = {"id": str, "description": str, "rules": [rule name, ...], "bouts": [bout record, ...] (date order),
          "expected": {"bouts": [log row per bout, same order], "fighters": [export row, ...]}}
  log row = {fight_id, date, fighter_a, fighter_b,
             pre_a, pre_b  rating going in, decayed to the day (1500 for a debutant),
             k_a, k_b      K from n before the day,
             e_a           fighter_a's expected score,
             w_a           fighter_a's result score, null = no update (both deltas 0),
             delta_a, delta_b  this bout's change,
             post_a, post_b    stored rating after the day (every bout of one day shows the end-of-day value),
             peak_a, peak_b    raw peak after the bout (null before the 5th UFC bout)}
  case "names" = {fighter id: display name} for every fighter in the case (the best win's name comes from it).
  export row = one row of research's fighter_rating.parquet (`COLUMNS`), fighters sorted by id, peak/gender null if unknown,
  best_win_* all null when the fighter has no win.
Schema changes that only add keys or columns keep format_version 1 (readers select by name); renaming,
removing or changing the meaning of a field bumps it.
Each case is independent: start from an empty population (everyone a debutant), apply the bouts in order, and
the per-bout log and final rows must match to ~1e-9. Fighter ids are local to the case.

Regenerate the golden cases (tests/score/fixtures/rating_cases.json): `uv run python -m fightcard.history.rating
<path>`.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd

from fightcard.history.store import names as store_names
from fightcard.history.store import rating_bouts

SCOPE = ("ufc", "full_card")  # store.rating_bouts: UFC events, plus other cards with >= MIN_CARD_BOUTS bouts
FORMAT_VERSION = 1
START = 1500.0
K_NUM, K_OFFSET, K_EXP = 275.0, 5, 0.4
W_SPLIT, W_MAJORITY = 0.6, 0.8
DECAY_PER_YEAR, DAYS_PER_YEAR = 0.9, 365.25
PEAK_MIN_UFC_BOUTS = 5
NO_UPDATE_METHODS = ("CNC", "Overturned")
UNGENDERED_CLASSES = {"Catch Weight", "Open Weight"}
COLUMNS = ["fighter_id", "rating", "last_bout_date", "n_bouts", "n_ufc_bouts", "peak", "gender",
           "best_win_opponent_id", "best_win_opponent", "best_win_rating", "best_win_date"]

PARAMS = {
    "start": START, "k_numerator": K_NUM, "k_offset": K_OFFSET, "k_exponent": K_EXP, "w_split": W_SPLIT,
    "w_majority": W_MAJORITY, "decay_per_year": DECAY_PER_YEAR, "days_per_year": DAYS_PER_YEAR,
    "peak_min_ufc_bouts": PEAK_MIN_UFC_BOUTS, "no_update_methods": list(NO_UPDATE_METHODS),
    "scope": list(SCOPE),
    "display": {"anchor_min_bouts": 3, "anchor_active_days": 730, "p4p_min_ufc_bouts": 5, "active_days": 365,
                "p4p_men": 15, "p4p_women": 10, "provisional_below_ufc_bouts": 3},
}


def k_factor(n: int) -> float:
    return K_NUM / (n + K_OFFSET) ** K_EXP


def expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def decayed(r: float, days: float) -> float:
    if days <= 0:
        return r
    return START + (r - START) * DECAY_PER_YEAR ** (days / DAYS_PER_YEAR)


def result_score(result: str, method: str | None) -> float | None:
    """fighter_a's W, or None for no update."""
    if result not in ("win", "loss", "draw", "nc"):
        raise ValueError(f"unknown result {result!r}")
    if result == "nc" or method in NO_UPDATE_METHODS:
        return None
    if result == "draw":
        return 0.5
    w = {"S-DEC": W_SPLIT, "M-DEC": W_MAJORITY}.get(method, 1.0)
    return w if result == "win" else 1 - w


def _gender(classes: set) -> str | None:
    named = {c for c in classes if isinstance(c, str) and c not in UNGENDERED_CLASSES}
    if any(c.startswith("Women's") for c in named):
        return "F"
    return "M" if named else None


def rate(bouts: list[dict], names: dict | None = None) -> tuple[list[dict], pd.DataFrame]:
    """Chronological pass over bout records; returns the per-bout log (sorted by date, fight_id) and the
    export rows (`COLUMNS`, one per fighter, sorted by id). `names` gives the best win's display name."""
    names = names or {}
    bouts = sorted(bouts, key=lambda b: (b["date"], b["fight_id"]))
    R, n, n_ufc, last, peak, classes, best = {}, {}, {}, {}, {}, {}, {}
    log: list[dict] = []
    i = 0
    while i < len(bouts):
        day = bouts[i]["date"]
        j = i
        while j < len(bouts) and bouts[j]["date"] == day:
            j += 1
        today = date.fromisoformat(day)
        pre: dict = {}
        for b in bouts[i:j]:
            for f in (b["fighter_a"], b["fighter_b"]):
                if f not in pre:
                    pre[f] = decayed(R[f], (today - last[f]).days) if f in R else START
        delta: dict = {}
        rows = []
        for b in bouts[i:j]:
            a, o = b["fighter_a"], b["fighter_b"]
            ea, ka, ko = expected(pre[a], pre[o]), k_factor(n.get(a, 0)), k_factor(n.get(o, 0))
            w = result_score(b["result"], b["method"])
            if w is not None and b["result"] in ("win", "loss"):
                winner, loser = (a, o) if b["result"] == "win" else (o, a)
                if winner not in best or pre[loser] >= best[winner][0]:  # sorted order: ties go to the later bout
                    best[winner] = (pre[loser], loser, day)
            da = db = 0.0
            if w is not None:
                da, db = ka * (w - ea), ko * ((1 - w) - (1 - ea))
                delta[a] = delta.get(a, 0.0) + da
                delta[o] = delta.get(o, 0.0) + db
            rows.append({"fight_id": b["fight_id"], "date": day, "fighter_a": a, "fighter_b": o, "pre_a": pre[a],
                         "pre_b": pre[o], "k_a": ka, "k_b": ko, "e_a": ea, "w_a": w, "delta_a": da, "delta_b": db})
        for f, r in pre.items():
            R[f] = r + delta.get(f, 0.0) if f in delta else r
            last[f] = today
        for b, row in zip(bouts[i:j], rows):
            for f, side in ((b["fighter_a"], "a"), (b["fighter_b"], "b")):
                n[f] = n.get(f, 0) + 1
                n_ufc[f] = n_ufc.get(f, 0) + bool(b["ufc"])
                classes.setdefault(f, set()).add(b["weight_class"])
                if n_ufc[f] >= PEAK_MIN_UFC_BOUTS:
                    peak[f] = max(peak.get(f, R[f]), R[f])
                row[f"post_{side}"], row[f"peak_{side}"] = R[f], peak.get(f)
        log.extend(rows)
        i = j
    ids = sorted(R)
    fighters = pd.DataFrame({
        "fighter_id": pd.Series(ids, dtype=object),
        "rating": np.array([R[f] for f in ids], dtype=np.float64),
        "last_bout_date": pd.Series([last[f].isoformat() for f in ids], dtype=object),
        "n_bouts": np.array([n[f] for f in ids], dtype=np.int64),
        "n_ufc_bouts": np.array([n_ufc[f] for f in ids], dtype=np.int64),
        "peak": np.array([peak.get(f, np.nan) for f in ids], dtype=np.float64),
        "gender": pd.Series([_gender(classes[f]) for f in ids], dtype=object),
        "best_win_opponent_id": pd.Series([best[f][1] if f in best else None for f in ids], dtype=object),
        "best_win_opponent": pd.Series([names.get(best[f][1]) if f in best else None for f in ids], dtype=object),
        "best_win_rating": np.array([best[f][0] if f in best else np.nan for f in ids], dtype=np.float64),
        "best_win_date": pd.Series([best[f][2] if f in best else None for f in ids], dtype=object),
    }, columns=COLUMNS)
    return log, fighters


def records(fighters: pd.DataFrame) -> list[dict]:
    """Export rows as JSON-safe dicts with exact floats (NaN peak -> None)."""
    out = []
    for r in fighters.to_dict("records"):
        out.append({"fighter_id": r["fighter_id"], "rating": float(r["rating"]),
                    "last_bout_date": r["last_bout_date"], "n_bouts": int(r["n_bouts"]),
                    "n_ufc_bouts": int(r["n_ufc_bouts"]), "peak": None if pd.isna(r["peak"]) else float(r["peak"]),
                    "gender": r["gender"], "best_win_opponent_id": r["best_win_opponent_id"],
                    "best_win_opponent": r["best_win_opponent"],
                    "best_win_rating": None if pd.isna(r["best_win_rating"]) else float(r["best_win_rating"]),
                    "best_win_date": r["best_win_date"]})
    return out


# -- golden cases --------------------------------------------------------------------------------------

def _b(day, fid, a, b, result="win", method="KO/TKO", ufc=True, wc="Lightweight") -> dict:
    return {"date": day, "fight_id": fid, "fighter_a": a, "fighter_b": b, "result": result, "method": method,
            "ufc": ufc, "weight_class": wc}


CASES = [
    {"id": "debut", "rules": ["debut_k"],
     "description": "Two debutants at 1500 with K = 275 / 5^0.4; a KO win moves each by K/2.",
     "bouts": [_b("2020-01-01", "f1", "A", "B")]},
    {"id": "decay_gap", "rules": ["decay_gap"],
     "description": "A wins, then fights again 1096 days later: pre-rating decayed 0.9^(1096/365.25) toward 1500, "
                    "K from n = 1.",
     "bouts": [_b("2020-01-01", "f1", "A", "B"), _b("2023-01-01", "f2", "A", "C")]},
    {"id": "same_day", "rules": ["same_day"],
     "description": "Tournament day: A beats B, C beats D, then A beats C, all from pre-day ratings and pre-day K; "
                    "n advances by 2 after the day, so A's next bout uses n = 2.",
     "bouts": [_b("2020-01-01", "f1", "A", "B"), _b("2020-01-01", "f2", "C", "D"), _b("2020-01-01", "f3", "A", "C"),
               _b("2020-02-01", "f4", "A", "E")]},
    {"id": "split_majority", "rules": ["split_credit", "majority_credit"],
     "description": "S-DEC win scores 0.6, M-DEC win 0.8, an S-DEC loss 0.4 for the loser's side.",
     "bouts": [_b("2020-01-01", "f1", "A", "B", method="S-DEC"), _b("2020-01-01", "f2", "C", "D", method="M-DEC"),
               _b("2020-01-01", "f3", "E", "F", result="loss", method="S-DEC")]},
    {"id": "favourite_split_win", "rules": ["favourite_split_win"],
     "description": "After two KO wins A expects > 0.6 against a debutant, so an S-DEC win (0.6) costs A points.",
     "bouts": [_b("2020-01-01", "f1", "A", "B"), _b("2020-01-15", "f2", "A", "C"),
               _b("2020-02-01", "f3", "A", "D", method="S-DEC")]},
    {"id": "draw", "rules": ["draw"],
     "description": "A favourite who draws loses points; a split draw between equals changes nothing.",
     "bouts": [_b("2020-01-01", "f1", "A", "B"), _b("2020-02-01", "f2", "A", "C", result="draw", method="U-DEC"),
               _b("2020-02-01", "f3", "G", "H", result="draw", method="S-DEC")]},
    {"id": "nc_counts_n", "rules": ["nc_counts_n"],
     "description": "An NC gives no update but counts toward n (next K uses n = 2) and restarts the decay clock "
                    "(the stored rating is decayed to the NC day).",
     "bouts": [_b("2020-01-01", "f1", "A", "B"), _b("2020-07-01", "f2", "A", "C", result="nc"),
               _b("2021-07-01", "f3", "A", "D")]},
    {"id": "cnc_overturned", "rules": ["cnc_draw_no_update", "overturned_no_update", "dq_win"],
     "description": "A CNC recorded as a draw and an Overturned recorded as a win give no update but count toward "
                    "n; a DQ win scores 1.",
     "bouts": [_b("2020-01-01", "f1", "A", "B"), _b("2020-01-01", "f2", "E", "F", method="DQ"),
               _b("2020-03-01", "f3", "A", "C", result="draw", method="CNC"),
               _b("2020-05-01", "f4", "D", "A", result="win", method="Overturned"), _b("2020-08-01", "f5", "A", "E")]},
    {"id": "peak_fifth_ufc", "rules": ["peak_fifth_ufc", "non_ufc_counts_n"],
     "description": "P's 3rd bout is non-UFC: it counts toward n but not n_ufc_bouts. The peak is null until P's "
                    "5th UFC bout (a loss, 6th bout overall), so the higher rating before it is not the peak; a "
                    "later non-UFC split win (a points loss for the favourite) still counts toward the peak.",
     "bouts": [_b("2020-01-01", "p1", "P", "O1"), _b("2020-03-01", "p2", "P", "O2"),
               _b("2020-05-01", "p3", "P", "O3", ufc=False), _b("2020-07-01", "p4", "P", "O4"),
               _b("2020-09-01", "p5", "P", "O5"), _b("2020-11-01", "p6", "P", "O6", result="loss"),
               _b("2021-01-01", "p7", "P", "O7", method="S-DEC", ufc=False)]},
    {"id": "gender", "rules": ["gender"],
     "description": "Any Women's division -> F; catch/open weight or a missing class alone -> null; any other "
                    "division -> M.",
     "bouts": [_b("2020-01-01", "g1", "W1", "W2", wc="Women's Flyweight"),
               _b("2020-02-01", "g2", "W1", "X", wc="Catch Weight"),
               _b("2020-03-01", "g3", "X", "Y", wc="Catch Weight"),
               _b("2020-04-01", "g4", "M1", "Y", wc="Lightweight"), _b("2020-05-01", "g5", "Z", "Q", wc=None)]},
    {"id": "best_win_split", "rules": ["best_win_split", "best_win_excludes"],
     "description": "D and G are built up. A's best win is D, beaten by split decision, at D's rating going in "
                    "(decayed over 335 days). A's draw with the stronger G, the NC, the CNC win and the Overturned win "
                    "over G don't count, and neither does A's loss to G (G's best win is then A).",
     "bouts": [_b("2020-01-01", "f1", "A", "B"), _b("2020-01-01", "f2", "D", "E"), _b("2020-02-01", "f3", "D", "F"),
               _b("2020-01-01", "f4", "G", "H"), _b("2020-02-01", "f5", "G", "I"), _b("2020-03-01", "f6", "G", "J"),
               _b("2021-01-01", "f7", "A", "D", method="S-DEC"),
               _b("2021-02-01", "f8", "A", "G", result="draw", method="U-DEC"),
               _b("2021-03-01", "f9", "A", "G", result="nc"), _b("2021-04-01", "f10", "A", "G", method="CNC"),
               _b("2021-05-01", "f11", "A", "G", method="Overturned"), _b("2021-06-01", "f12", "G", "A")]},
    {"id": "best_win_tie", "rules": ["best_win_tie"],
     "description": "A beats two debutants (both 1500 going in); the tie goes to the more recent win (C, where A "
                    "is fighter_b). B, C and D have no win: best_win_* null.",
     "bouts": [_b("2020-01-01", "f1", "A", "B"), _b("2020-02-01", "f2", "C", "A", result="loss"),
               _b("2020-03-01", "f3", "D", "A", result="draw", method="S-DEC")]},
]


def golden_cases() -> dict:
    cases = []
    for c in CASES:
        names = {f: f"Fighter {f}" for b in c["bouts"] for f in (b["fighter_a"], b["fighter_b"])}
        log, fighters = rate(c["bouts"], names)
        cases.append({**c, "names": names, "expected": {"bouts": log, "fighters": records(fighters)}})
    return {"format_version": FORMAT_VERSION, "params": PARAMS, "cases": cases}


def _dump(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


class HistoryPass(NamedTuple):
    """`history_pass`'s result: `rate`'s export rows indexed by fighter_id, the bouts they were rated from, names, and
    `rate`'s per-bout log (`fightcard.history.timeline` answers "as of any date" from it)."""
    ratings: pd.DataFrame
    bouts: list[dict]
    names: dict[str, str]
    log: list[dict]


def history_pass(conn: sqlite3.Connection, as_of: str | None = None) -> HistoryPass:
    """The history pass up to `as_of` (None = all), the one `score`, `images` and `fightcard history rankings` share
    so they agree on who is rated."""
    bouts, who = rating_bouts(conn, as_of), store_names(conn)
    log, fighters = rate(bouts, who)
    return HistoryPass(fighters.set_index("fighter_id"), bouts, who, log)


def history_ratings(conn: sqlite3.Connection, as_of: str | None = None) -> pd.DataFrame:
    """`rate` over the store's bouts up to `as_of` (None = all): the export rows indexed by fighter_id."""
    return history_pass(conn, as_of).ratings


if __name__ == "__main__":
    # uv run python -m fightcard.history.rating <path>: regenerate the golden cases
    Path(sys.argv[1]).write_text(_dump(golden_cases()))
