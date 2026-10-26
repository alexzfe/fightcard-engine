"""Fighter rating: the research export rolled forward over newer results.

Formula and exact semantics: docs/ELO.md (Contract). In short: start 1500,
`K = 275 / (n + 5)^0.4` with n = prior bouts in scope (NC included), win 1 /
draw 0.5 / loss 0 but a split-decision win is 0.6 and a majority-decision win
0.8 (the loser gets 1 − W), NC doesn't move ratings. Ratings decay 10% a year
toward 1500 from the last bout, applied lazily (the stored rating is the
undecayed post-bout value). All bouts on one day use the pre-day ratings and n.

`model/fighter_rating.parquet` holds every fighter's state as of
`manifest.as_of`. Bouts after that come from `recent_fights` of the fighters in
fighters.json; an opponent missing from the export starts as a debutant.

Display: everyone is decayed to now, then shifted so the mean of established
fighters (n ≥ 3, a bout in the last 730 days) is 1500. P4P ranks (top 15 men,
top 10 women) go to fighters with ≥5 UFC bouts and a bout in the last 365 days.

A fighter on the card is unrated (every field null / false) and left out of the
ranking when:
- there is no rating export, or
- they're not in the export and have no newer bouts, or
- the known newer bouts don't account for their whole record change since the
  export (more than 3 bouts since, or a bout without an opponent id).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import pandas as pd

from fightcard.models import Fighter, PastResult

INIT = 1500.0
DECAY_PER_YEAR, DAYS_PER_YEAR = 0.9, 365.25
K_NUMERATOR, K_OFFSET, K_EXPONENT = 275.0, 5, 0.4
WIN_CREDIT = {"S-DEC": 0.6, "M-DEC": 0.8}  # ufcstats methods; every other win is a full win
NO_UPDATE_METHODS = ("CNC", "Overturned")  # a no contest whatever the recorded outcome
FLIP = {"win": "loss", "loss": "win"}
NOT_UFC = ("dwcs", "road to ufc")  # lower-cased event-name prefixes of in-scope bouts that aren't UFC bouts
NO_DIVISION = ("catch", "open")  # weight classes that say nothing about gender
ESTABLISHED_N, ANCHOR_DAYS = 3, 730
P4P_UFC_BOUTS, ACTIVE_DAYS, P4P_SIZE = 5, 365, {"M": 15, "F": 10}
PROVISIONAL_UFC_BOUTS, PEAK_UFC_BOUTS = 3, 5


@dataclass(frozen=True)
class Win:
    """A fighter's best win: the beaten opponent rated highest going in (raw, decayed to the bout day)."""

    opponent_id: str
    opponent: str | None
    rating: float
    date: str


@dataclass
class State:
    """One fighter's raw rating state between bouts."""

    rating: float = INIT  # undecayed post-bout value
    n: int = 0  # bouts in scope, NC included
    n_ufc: int = 0
    last: str | None = None  # YYYY-MM-DD of the last bout
    peak: float | None = None  # highest post-bout rating from the 5th UFC bout on
    gender: str | None = None  # "M" / "F"
    best_win: Win | None = None


@dataclass(frozen=True)
class Bout:
    date: str  # YYYY-MM-DD
    a: str
    b: str
    result: PastResult  # from a's side
    method: str | None = None  # ufcstats method, e.g. "S-DEC"
    ufc: bool = True
    weight_class: str | None = None  # sets gender: any Women's division -> "F", else a named division -> "M"
    name_a: str | None = None  # display names, for the best win
    name_b: str | None = None


@dataclass(frozen=True)
class Step:
    """What one bout did: each side's K, pre-day (decayed) rating, a's expected and result score (None = no
    update), the change, and rating and raw peak after the day. Same fields as a rating_cases.json log row."""

    bout: Bout
    k_a: float
    k_b: float
    pre_a: float
    pre_b: float
    e_a: float
    w_a: float | None
    delta_a: float
    delta_b: float
    post_a: float
    post_b: float
    peak_a: float | None
    peak_b: float | None


def k_factor(n: int) -> float:
    return K_NUMERATOR / (n + K_OFFSET) ** K_EXPONENT


def credit(result: PastResult, method: str | None) -> float | None:
    """Result score W for the fighter whose result this is; None for a no contest."""
    if result == "nc" or method in NO_UPDATE_METHODS:
        return None
    if result == "draw":
        return 0.5
    w = WIN_CREDIT.get(method or "", 1.0)
    return w if result == "win" else 1.0 - w


def decay(rating: float, last: str | None, day: str) -> float:
    if last is None:
        return rating
    days = (date.fromisoformat(day) - date.fromisoformat(last)).days
    return INIT + (rating - INIT) * DECAY_PER_YEAR ** (days / DAYS_PER_YEAR)


def expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def _best_win(s: State, win: Win) -> None:
    """Keep the higher-rated opponent; a tie goes to the later bout (bouts arrive in date order)."""
    if s.best_win is None or win.rating >= s.best_win.rating:
        s.best_win = win


def _gender(gender: str | None, weight_class: str | None) -> str | None:
    wc = (weight_class or "").lower()
    if "women" in wc:
        return "F"
    if gender is None and wc and not wc.startswith(NO_DIVISION):
        return "M"
    return gender


def roll_forward(states: dict[str, State], bouts: Iterable[Bout]) -> list[Step]:
    """Apply `bouts` to `states` in place (a missing fighter starts as a debutant); one Step per bout, by date."""
    by_day: dict[str, list[Bout]] = defaultdict(list)
    for b in bouts:
        by_day[b.date].append(b)
    steps: list[Step] = []
    for day in sorted(by_day):
        fought: dict[str, list[Bout]] = defaultdict(list)
        for b in by_day[day]:
            fought[b.a].append(b)
            fought[b.b].append(b)
        for fid in fought:  # decay each fighter to the day; n is still the pre-day count
            s = states.setdefault(fid, State())
            s.rating = decay(s.rating, s.last, day)
        delta: dict[str, float] = defaultdict(float)
        todo = []
        for b in by_day[day]:
            sa, sb = states[b.a], states[b.b]
            ka, kb = k_factor(sa.n), k_factor(sb.n)
            w, ea = credit(b.result, b.method), expected(sa.rating, sb.rating)
            da = 0.0 if w is None else ka * (w - ea)
            db = 0.0 if w is None else kb * ((1.0 - w) - (1.0 - ea))
            delta[b.a] += da
            delta[b.b] += db
            if w is not None and w != 0.5:  # a win (split / majority included) for one side
                winner, loser, name = (sa, b.b, b.name_b) if w > 0.5 else (sb, b.a, b.name_a)
                _best_win(winner, Win(loser, name, states[loser].rating, day))
            todo.append((b, ka, kb, sa.rating, sb.rating, ea, w, da, db))
        for fid, today in fought.items():
            s = states[fid]
            s.rating += delta[fid]
            s.n += len(today)
            s.n_ufc += sum(b.ufc for b in today)
            s.last = day
            if s.n_ufc >= PEAK_UFC_BOUTS:  # any bout, UFC or not, once the fighter has 5 UFC bouts
                s.peak = s.rating if s.peak is None else max(s.peak, s.rating)
            for b in today:
                s.gender = _gender(s.gender, b.weight_class)
        steps += [Step(b, ka, kb, ra, rb, ea, w, da, db, states[b.a].rating, states[b.b].rating,
                       states[b.a].peak, states[b.b].peak)
                  for b, ka, kb, ra, rb, ea, w, da, db in todo]
    return steps


def states_from_export(ratings: pd.DataFrame) -> dict[str, State]:
    """`ratings` is model/fighter_rating.parquet indexed by fighter_id."""
    out = {}
    for fid, r in ratings.iterrows():
        peak, gender = _value(r, "peak"), _value(r, "gender")
        best = None
        if _value(r, "best_win_opponent_id") is not None:  # columns absent from older exports
            name = _value(r, "best_win_opponent")
            best = Win(str(r["best_win_opponent_id"]), None if name is None else str(name),
                       float(r["best_win_rating"]), str(r["best_win_date"]))
        out[str(fid)] = State(rating=float(r["rating"]), n=int(r["n_bouts"]), n_ufc=int(r["n_ufc_bouts"]),
                              last=str(r["last_bout_date"]), peak=None if peak is None else float(peak),
                              gender=None if gender is None else str(gender), best_win=best)
    return out


def _value(r: pd.Series, column: str):
    """A row's value, or None when the column is missing or null."""
    v = r.get(column)
    return None if v is None or pd.isna(v) else v


def _newer_bouts(fighters: dict[str, Fighter], as_of: str) -> tuple[list[Bout], dict[str, int], set[str]]:
    """Each bout after `as_of` once (both fighters may list it), the newer bouts found per fighter, and the
    fighters with a newer bout that has no opponent id."""
    bouts: dict[tuple[str, str, str], Bout] = {}
    known: dict[str, int] = defaultdict(int)
    missing: set[str] = set()
    for fid, f in fighters.items():
        for p in f.recent_fights or []:
            if p.date is None or p.date <= as_of:
                continue
            if p.opponent_id is None:
                missing.add(fid)
                continue
            known[fid] += 1
            a, b = sorted((fid, p.opponent_id))
            result = p.result if a == fid else FLIP.get(p.result, p.result)
            names = (f.name, p.opponent) if a == fid else (p.opponent, f.name)
            bouts[(p.date, a, b)] = Bout(p.date, a, b, result, p.method, ufc=not p.event.lower().startswith(NOT_UFC),
                                         name_a=names[0], name_b=names[1])
    return list(bouts.values()), known, missing


def _unrated() -> dict:
    return {"elo": None, "elo_peak": None, "p4p_rank": None, "elo_provisional": False, "elo_inactive": False,
            "best_win": None}


def fighter_ratings(fighters: dict[str, Fighter], ratings: pd.DataFrame | None, snapshot: pd.DataFrame,
                    as_of: str, now: date) -> dict[str, dict]:
    """The Fighter rating fields (`elo`, `elo_peak`, `p4p_rank`, `elo_provisional`, `elo_inactive`, `best_win`)
    for each fighter in `fighters`.

    `ratings` is model/fighter_rating.parquet indexed by fighter_id (None when absent); `snapshot` is indexed
    by fighter_id with record_w/l/d/nc as of the export."""
    if ratings is None:
        return {fid: _unrated() for fid in fighters}
    states = states_from_export(ratings)
    exported = set(states)
    bouts, known, missing = _newer_bouts(fighters, as_of)
    roll_forward(states, bouts)

    unrated = set()
    for fid, f in fighters.items():
        r = f.record
        since = r.w + r.l + r.d + r.nc
        if fid in snapshot.index:
            row = snapshot.loc[fid]
            since -= int(row["record_w"] + row["record_l"] + row["record_d"] + row["record_nc"])
        if (fid not in exported and known[fid] == 0) or fid in missing or known[fid] != since:
            unrated.add(fid)

    today = now.isoformat()
    days = {fid: (now - date.fromisoformat(s.last)).days if s.last else None for fid, s in states.items()}
    current = {fid: decay(s.rating, s.last, today) for fid, s in states.items()}
    anchor = [current[fid] for fid, s in states.items()
              if s.n >= ESTABLISHED_N and days[fid] is not None and days[fid] <= ANCHOR_DAYS]
    shift = INIT - sum(anchor) / len(anchor) if anchor else 0.0

    def active(fid: str) -> bool:
        return days[fid] is not None and days[fid] <= ACTIVE_DAYS

    rank: dict[str, int] = {}
    for gender, size in P4P_SIZE.items():
        pool = [fid for fid, s in states.items()
                if s.gender == gender and s.n_ufc >= P4P_UFC_BOUTS and active(fid) and fid not in unrated]
        pool.sort(key=lambda fid: (-current[fid], fid))
        rank |= {fid: i for i, fid in enumerate(pool[:size], start=1)}

    out = {}
    for fid in fighters:
        if fid in unrated:
            out[fid] = _unrated()
            continue
        s = states[fid]
        out[fid] = {"elo": round(current[fid] + shift),
                    "elo_peak": None if s.peak is None else round(s.peak + shift),
                    "p4p_rank": rank.get(fid),
                    "elo_provisional": s.n_ufc < PROVISIONAL_UFC_BOUTS,
                    "elo_inactive": not active(fid),
                    "best_win": None if s.best_win is None else {
                        "opponent_id": s.best_win.opponent_id, "opponent": s.best_win.opponent or s.best_win.opponent_id,
                        "elo": round(s.best_win.rating + shift), "date": s.best_win.date}}
    return out
