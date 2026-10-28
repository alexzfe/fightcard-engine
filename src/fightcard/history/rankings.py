"""Rankings over every rated fighter, on the displayed scale (`fightcard.history.display`): the P4P lists, the
active-fighter index the site's search reads, division ranks and the all-time peak list. `size_report` measures each
as compact JSON, so we can see what the site can carry.

`ratings` is `rating.history_pass`'s `ratings` (indexed by fighter_id); `names` is `store.names`; `divs` is `divisions`.
A fighter without a name shows their id. `published` is the part of them that goes to data/rankings.json.
"""

from __future__ import annotations

import gzip
import json
from datetime import date

import pandas as pd

from fightcard.history.display import ACTIVE_DAYS, PROVISIONAL_UFC_BOUTS, p4p_ranks, rankable, scale
from fightcard.history.rating import UNGENDERED_CLASSES

DIVISION_SIZE = 15
P4P_LISTS = {"men": "M", "women": "F"}


def divisions(bouts: list[dict]) -> dict[str, str]:
    """Each fighter's division: the weight class of their latest bout (`store.rating_bouts` records), skipping
    Catch Weight, Open Weight and unknown classes. A fighter with only those has none."""
    out: dict[str, str] = {}
    for b in sorted(bouts, key=lambda b: (b["date"], b["fight_id"])):
        wc = b["weight_class"]
        if wc and wc not in UNGENDERED_CLASSES:
            out[b["fighter_a"]] = out[b["fighter_b"]] = wc
    return out


def _elo(current: dict[str, float], shift: float, fid: str) -> int:
    return round(current[fid] + shift)


def p4p(ratings: pd.DataFrame, names: dict, divs: dict, now: date) -> dict[str, list[dict]]:
    """{"men": top 15, "women": top 10}, the same ranks and Elo the fighter pages show."""
    days, current, shift = scale(ratings, now)
    rank = p4p_ranks(ratings, days, current)
    out: dict[str, list[dict]] = {}
    for key, gender in P4P_LISTS.items():
        ids = sorted((fid for fid in rank if ratings.at[fid, "gender"] == gender), key=rank.__getitem__)
        out[key] = [{"rank": rank[fid], "id": fid, "name": names.get(fid, fid), "elo": _elo(current, shift, fid),
                     "division": divs.get(fid)} for fid in ids]
    return out


def active_index(ratings: pd.DataFrame, names: dict, divs: dict, now: date) -> list[dict]:
    """Every fighter with a UFC bout and a bout in ACTIVE_DAYS, best first (ties by id)."""
    days, current, shift = scale(ratings, now)
    rank = p4p_ranks(ratings, days, current)
    ids = [fid for fid, n in ratings["n_ufc_bouts"].items() if n >= 1 and days[fid] <= ACTIVE_DAYS]
    ids.sort(key=lambda fid: (-current[fid], fid))
    return [{"id": fid, "name": names.get(fid, fid), "elo": _elo(current, shift, fid), "division": divs.get(fid),
             "p4p_rank": rank.get(fid), "provisional": bool(ratings.at[fid, "n_ufc_bouts"] < PROVISIONAL_UFC_BOUTS)}
            for fid in ids]


def division_ranks(ratings: pd.DataFrame, names: dict, divs: dict, now: date,
                   size: int = DIVISION_SIZE) -> dict[str, list[dict]]:
    """{division: its top `size`}, with the P4P eligibility; fighters without a division are left out."""
    days, current, shift = scale(ratings, now)
    out: dict[str, list[dict]] = {}
    for fid in rankable(ratings, days, current):
        div = divs.get(fid)
        if div is None or len(out.setdefault(div, [])) >= size:
            continue
        out[div].append({"rank": len(out[div]) + 1, "id": fid, "name": names.get(fid, fid),
                         "elo": _elo(current, shift, fid)})
    return out


def all_time_peaks(ratings: pd.DataFrame, names: dict, now: date, limit: int | None = None) -> list[dict]:
    """Every fighter with a peak (retired included), highest first (ties by id), cut to `limit` if given."""
    _, _, shift = scale(ratings, now)
    peaks = ratings["peak"].dropna()
    ids = sorted(peaks.index, key=lambda fid: (-peaks[fid], fid))[:limit]
    return [{"rank": i, "id": fid, "name": names.get(fid, fid), "peak": round(float(peaks[fid]) + shift)}
            for i, fid in enumerate(ids, start=1)]


def published(ratings: pd.DataFrame, names: dict, divs: dict, now: date, headshots: set[str]) -> dict:
    """data/rankings.json (docs/CONTRACT.md): the P4P lists and the active index as of `now`. A P4P entry's `image` is
    `images/<id>.webp` when the id is in `headshots`, else null; active rows carry none."""
    lists = p4p(ratings, names, divs, now)
    return {"as_of": now.isoformat(),
            "p4p": {key: [e | {"image": f"images/{e['id']}.webp" if e["id"] in headshots else None} for e in es]
                    for key, es in lists.items()},
            "active": active_index(ratings, names, divs, now)}


def _rows(value) -> int:
    return sum(len(v) for v in value.values()) if isinstance(value, dict) else len(value)


def size_report(modules: dict[str, object]) -> list[dict]:
    """Per module: rows, and bytes as compact JSON, raw and gzipped."""
    out = []
    for name, value in modules.items():
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
        out.append({"module": name, "rows": _rows(value), "raw_bytes": len(raw),
                    "gzip_bytes": len(gzip.compress(raw, mtime=0))})
    return out
