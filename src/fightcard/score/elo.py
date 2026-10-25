"""Current Elo per fighter: the snapshot rating rolled forward over newer results.

Same Elo as fight-auto-research (`build/toolkit.py` `elo_update` and
`chrono_rating`): K=32, start 1500, 400-point logistic; win 1, draw 0.5, loss 0;
NC doesn't update; all bouts on one day see the ratings from before that day.

The snapshot's `elo` is each fighter's rating going into their next bout as of
the export (`manifest.as_of`). Bouts after that come from `recent_fights` of the
fighters in fighters.json. An opponent who isn't in fighters.json starts from
their snapshot rating and only moves with the bouts we know about.

A fighter gets None when:
- they have no UFC bouts in the snapshot and none since, or
- the known newer bouts don't account for their whole record change since the
  export (more than 3 bouts since, or a bout without an opponent id).
"""

from __future__ import annotations

from collections import defaultdict

import pandas as pd

from fightcard.models import Fighter

K, INIT = 32.0, 1500.0
SCORE = {"win": 1.0, "loss": 0.0, "draw": 0.5}
FLIP = {"win": "loss", "loss": "win"}


def update(ra: float, rb: float, score_a: float) -> float:
    """Elo: change for fighter A given ratings and A's score (1 / 0.5 / 0)."""
    return K * (score_a - 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0)))


def current_elo(fighters: dict[str, Fighter], snapshot: pd.DataFrame, as_of: str) -> dict[str, int | None]:
    """Rounded current Elo for each fighter in `fighters` (None when unknown).

    `snapshot` is indexed by fighter_id with `elo`, `n_ufc_bouts` and record_w/l/d/nc."""
    # (date, a, b) with a < b -> a's result; each bout is listed by both fighters
    bouts: dict[tuple[str, str, str], str] = {}
    known: dict[str, int] = defaultdict(int)  # newer bouts found per card fighter
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
            bouts[(p.date, a, b)] = p.result if a == fid else FLIP.get(p.result, p.result)

    def start(fid: str) -> float:
        return float(snapshot.loc[fid, "elo"]) if fid in snapshot.index else INIT

    rating: dict[str, float] = {}
    by_day: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for (date, a, b), res in bouts.items():
        by_day[date].append((a, b, res))
    for date in sorted(by_day):
        delta: dict[str, float] = defaultdict(float)
        for a, b, res in by_day[date]:
            ra, rb = rating.setdefault(a, start(a)), rating.setdefault(b, start(b))
            if res == "nc":
                continue
            change = update(ra, rb, SCORE[res])
            delta[a] += change
            delta[b] -= change
        for fid, d in delta.items():
            rating[fid] += d

    out: dict[str, int | None] = {}
    for fid, f in fighters.items():
        r = f.record
        since = r.w + r.l + r.d + r.nc
        ufc = 0
        if fid in snapshot.index:
            row = snapshot.loc[fid]
            since -= int(row["record_w"] + row["record_l"] + row["record_d"] + row["record_nc"])
            ufc = int(row["n_ufc_bouts"])
        if (ufc == 0 and known[fid] == 0) or fid in missing or known[fid] != since:
            out[fid] = None
        else:
            out[fid] = round(rating.get(fid, start(fid)))
    return out


# Contract rating (docs/ELO.md): stubs
class State:
    def __init__(self, **kw):
        raise NotImplementedError


class Bout:
    def __init__(self, *a, **kw):
        raise NotImplementedError


def k_factor(n):
    raise NotImplementedError


def credit(result, method):
    raise NotImplementedError


def decay(rating, last, day):
    raise NotImplementedError


def roll_forward(states, bouts):
    raise NotImplementedError


def fighter_ratings(fighters, ratings, snapshot, as_of, now):
    raise NotImplementedError
