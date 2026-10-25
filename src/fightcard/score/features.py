"""Model input rows from fighter snapshots, per model/feature_spec.json.

Mirrors fight-auto-research `export.snapshot.assemble`: each feature's value per
fighter comes from the snapshot (directly, or by a date recipe evaluated at the
bout date), and the bout row holds order-free combinations of the two values.
model/examples.json pins the two implementations together.
"""

from __future__ import annotations

import math
from datetime import date as Date

import numpy as np
import pandas as pd

from fightcard.models import Event, Fight

NAN = float("nan")


def parse_location(location: str | None) -> tuple[str | None, str | None, str | None]:
    """ufcstats "City, State, Country" (or "City, Country") -> (city, state, country)."""
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    if not parts:
        return None, None, None
    if len(parts) == 1:
        return parts[0], None, None
    if len(parts) == 2:
        return parts[0], None, parts[1]
    return parts[0], ", ".join(parts[1:-1]), parts[-1]


def bout_context(event: Event, fight: Fight, position: int) -> dict:
    """Bout context the model was trained with. Scheduled rounds aren't on the
    card: main events (position 0) and title fights are five rounds."""
    city, state, country = parse_location(event.location)
    wc = fight.weight_class
    gender = None if wc is None else ("F" if wc.startswith("Women") else "M")
    return {"weight_class": wc, "gender": gender, "event_city": city, "event_state": state, "event_country": country,
            "sched_rounds": 5.0 if position == 0 or fight.title_fight else 3.0,
            "title_fight": float(fight.title_fight), "year": float(Date.fromisoformat(event.date).year)}


def _missing(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _days(a: pd.Timestamp, b) -> float:
    return NAN if _missing(b) else float((a - pd.Timestamp(b)).days)


def _value(row: pd.Series | None, source: dict, kind: str, day: pd.Timestamp):
    if row is None:
        return None if kind == "cat" else NAN
    k = source["kind"]
    if k == "static":
        v = row[source["column"]]
        if kind == "cat":
            return v
        return NAN if _missing(v) else float(v)
    if k == "age":
        age = _days(day, row[source["column"]]) / 365.25
        return age if source["min"] <= age <= source["max"] else NAN
    if k == "days_since":
        return _days(day, row[source["column"]])
    if k in ("window_count", "window_sum"):
        start = day - pd.Timedelta(days=int(source["days"]))
        keep = np.array([pd.Timestamp(d) >= start for d in row["recent_dates"]], dtype=bool)
        if k == "window_count":
            return float(keep.sum())
        return float(np.nansum(np.asarray(row[f"recent_{source['column']}"], dtype=float)[keep]))
    raise ValueError(f"unknown snapshot source kind {k!r}")


def _combine(a, b, how: str):
    if how == "pair":
        return "|".join(sorted((str(a), str(b))))
    if math.isnan(a) or math.isnan(b):
        return NAN
    if how == "min":
        return min(a, b)
    if how == "max":
        return max(a, b)
    if how == "sum":
        return a + b
    if how == "absdiff":
        return abs(a - b)
    raise ValueError(f"unknown combiner {how!r}")


def bout_row(snapshot: pd.DataFrame, spec: dict, red: str, blue: str, date, context: dict) -> dict:
    """One model input row: `context` plus every feature column of the spec.
    `snapshot` is indexed by fighter_id; a fighter not in it has unknown values."""
    day = pd.Timestamp(date)
    rows = [snapshot.loc[f] if f in snapshot.index else None for f in (red, blue)]
    out = dict(context)
    for e in spec["features"]:
        a, b = (_value(r, e["source"], e["kind"], day) for r in rows)
        for how in e["combiners"]:
            out[f"{e['name']}_{how}"] = _combine(a, b, how)
    return out


def encode(rows: list[dict], spec: dict) -> np.ndarray:
    """Numeric matrix in the booster's column order; categoricals become the
    index of their training level (NaN when missing or unseen)."""
    cols = spec["columns"]
    levels = {c: {v: i for i, v in enumerate(lv)} for c, lv in spec["categorical"].items()}
    A = np.full((len(rows), len(cols)), np.nan)
    for i, r in enumerate(rows):
        for j, c in enumerate(cols):
            v = r.get(c)
            if _missing(v):
                continue
            A[i, j] = levels[c].get(v, np.nan) if c in levels else float(v)
    return A
