"""Ratings as of any date, from the one history pass (`rating.history_pass`): its per-bout log, bouts and names.

"As of going into day D" means from the bouts strictly before D, so it equals a fresh `history_pass(as_of=D-1)`
(`rating_bouts` is inclusive). On the raw scale; the display shift is the caller's (`display.scale`).
  - `state(fighter_id, day)`: rating decayed to `day`, n, n_ufc, last bout date, gender, peak; None before a bout
  - `ratings(day)`: those rows for everyone as `rate`'s export rows (no best win), what `display` and `rankings` take
  - `p4p(day)`: `rankings.p4p` over them as of D-1, the lists `fightcard history rankings --as-of D-1` shows
  - `bout(fight_id)`: each fighter's pre (decayed to the day) and that bout's own delta; on an early-UFC tournament
    day every bout starts from the same pre-day rating, and the deltas sum to the day's change
  - `series(fighter_id)`: their bouts in date order; `post` is the end-of-day rating (shared by a day's bouts)
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import date, timedelta
from typing import NamedTuple

import numpy as np
import pandas as pd

from fightcard.history.rankings import divisions
from fightcard.history.rankings import p4p as p4p_lists
from fightcard.history.rating import COLUMNS, HistoryPass, _gender, decayed


class State(NamedTuple):
    rating: float
    n_bouts: int
    n_ufc_bouts: int
    last_bout_date: str
    gender: str | None
    peak: float | None


class Side(NamedTuple):
    pre: float
    delta: float


class _Day(NamedTuple):
    """A fighter after one of their bout days: the stored (undecayed) rating and the counts so far."""
    post: float
    n_bouts: int
    n_ufc_bouts: int
    gender: str | None
    peak: float | None


class Timeline:
    def __init__(self, log: list[dict], bouts: list[dict], names: dict[str, str] | None = None):
        self.names = names or {}
        self._bouts = sorted(bouts, key=lambda b: (b["date"], b["fight_id"]))
        self._bout_days = [b["date"] for b in self._bouts]
        by_id = {b["fight_id"]: b for b in self._bouts}
        self._log = {row["fight_id"]: row for row in log}
        self._series: dict[str, list[dict]] = {}
        self._days: dict[str, list[str]] = {}
        self._after: dict[str, list[_Day]] = {}
        n: dict[str, int] = {}
        n_ufc: dict[str, int] = {}
        classes: dict[str, set] = {}
        for row in sorted(log, key=lambda r: (r["date"], r["fight_id"])):
            b, day = by_id[row["fight_id"]], row["date"]
            for f, o, side in ((row["fighter_a"], row["fighter_b"], "a"), (row["fighter_b"], row["fighter_a"], "b")):
                n[f] = n.get(f, 0) + 1
                n_ufc[f] = n_ufc.get(f, 0) + bool(b["ufc"])
                classes.setdefault(f, set()).add(b["weight_class"])
                after = _Day(row[f"post_{side}"], n[f], n_ufc[f], _gender(classes[f]), row[f"peak_{side}"])
                days = self._days.setdefault(f, [])
                if days and days[-1] == day:
                    self._after[f][-1] = after  # a later bout the same day: the counts so far, the same post
                else:
                    days.append(day)
                    self._after.setdefault(f, []).append(after)
                self._series.setdefault(f, []).append({
                    "date": day, "fight_id": row["fight_id"], "opponent": o, "pre": row[f"pre_{side}"],
                    "delta": row[f"delta_{side}"], "post": row[f"post_{side}"]})
        self._p4p: dict[str, dict[str, list[dict]]] = {}

    @classmethod
    def from_pass(cls, hp: HistoryPass) -> Timeline:
        return cls(hp.log, hp.bouts, hp.names)

    def _going_in(self, fighter_id: str, day: str) -> tuple[str, _Day] | None:
        days = self._days.get(fighter_id)
        i = bisect_left(days, day) if days else 0
        return (days[i - 1], self._after[fighter_id][i - 1]) if i else None

    def state(self, fighter_id: str, day: str) -> State | None:
        """The fighter going into `day` (bouts strictly before it), decayed to `day`; None without one."""
        found = self._going_in(fighter_id, day)
        if found is None:
            return None
        last, d = found
        rating = decayed(d.post, (date.fromisoformat(day) - date.fromisoformat(last)).days)
        return State(rating, d.n_bouts, d.n_ufc_bouts, last, d.gender, d.peak)

    def ratings(self, day: str) -> pd.DataFrame:
        """`rate`'s export rows from the bouts strictly before `day`, indexed by fighter_id; the best win columns
        are null (the timeline doesn't keep them)."""
        rows = {f: found for f in sorted(self._days) if (found := self._going_in(f, day)) is not None}
        ids = list(rows)
        out = pd.DataFrame({
            "fighter_id": pd.Series(ids, dtype=object),
            "rating": np.array([rows[f][1].post for f in ids], dtype=np.float64),
            "last_bout_date": pd.Series([rows[f][0] for f in ids], dtype=object),
            "n_bouts": np.array([rows[f][1].n_bouts for f in ids], dtype=np.int64),
            "n_ufc_bouts": np.array([rows[f][1].n_ufc_bouts for f in ids], dtype=np.int64),
            "peak": np.array([np.nan if rows[f][1].peak is None else rows[f][1].peak for f in ids], dtype=np.float64),
            "gender": pd.Series([rows[f][1].gender for f in ids], dtype=object),
        }, columns=COLUMNS)
        return out.set_index("fighter_id")

    def p4p(self, day: str) -> dict[str, list[dict]]:
        """The P4P lists going into `day`: `rankings.p4p` over the bouts strictly before it, as of the day before."""
        if day not in self._p4p:
            before = self._bouts[:bisect_left(self._bout_days, day)]
            eve = date.fromisoformat(day) - timedelta(days=1)
            self._p4p[day] = p4p_lists(self.ratings(day), self.names, divisions(before), eve)
        return self._p4p[day]

    def bout(self, fight_id: str) -> dict[str, Side] | None:
        """{fighter_id: (pre, delta)} for both fighters of a rated bout; None when it isn't in the pass."""
        row = self._log.get(fight_id)
        if row is None:
            return None
        return {row["fighter_a"]: Side(row["pre_a"], row["delta_a"]),
                row["fighter_b"]: Side(row["pre_b"], row["delta_b"])}

    def series(self, fighter_id: str) -> list[dict]:
        """The fighter's bouts in date order: {date, fight_id, opponent, pre, delta, post}."""
        return list(self._series.get(fighter_id, []))
