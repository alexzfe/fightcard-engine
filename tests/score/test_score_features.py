"""Bout row assembly from fighter snapshots (mirrors fight-auto-research export.snapshot)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from fightcard.models import Event, Fight
from fightcard.score.features import bout_context, bout_row, encode, parse_location
from fightcard.score.bundle import tier_of


def _snap() -> pd.DataFrame:
    return pd.DataFrame([
        {"fighter_id": "a", "dob": "1990-01-01", "first_bout_date": "2015-01-01", "last_bout_date": "2026-01-01",
         "recent_dates": ["2024-11-01", "2025-06-01", "2026-01-01"], "recent_finish_loss": [1.0, float("nan"), 0.0],
         "elo": 1600.0, "stance": "Orthodox"},
        {"fighter_id": "b", "dob": None, "first_bout_date": "2020-01-01", "last_bout_date": "2025-01-01",
         "recent_dates": [], "recent_finish_loss": [], "elo": 1500.0, "stance": "Southpaw"},
    ]).set_index("fighter_id")


SPEC = {
    "columns": ["weight_class", "sched_rounds", "elo_min", "elo_absdiff", "stance_pair", "age_max", "layoff_days_max",
                "fights_2y_sum", "finished_2y_max"],
    "categorical": {"weight_class": ["Flyweight", "Lightweight"], "stance_pair": ["Orthodox|Southpaw"]},
    "features": [
        {"name": "elo", "kind": "num", "combiners": ["min", "absdiff"], "source": {"kind": "static", "column": "elo"}},
        {"name": "stance", "kind": "cat", "combiners": ["pair"], "source": {"kind": "static", "column": "stance"}},
        {"name": "age", "kind": "num", "combiners": ["max"],
         "source": {"kind": "age", "column": "dob", "min": 14.0, "max": 65.0}},
        {"name": "layoff_days", "kind": "num", "combiners": ["max"],
         "source": {"kind": "days_since", "column": "last_bout_date"}},
        {"name": "fights_2y", "kind": "num", "combiners": ["sum"], "source": {"kind": "window_count", "days": 730}},
        {"name": "finished_2y", "kind": "num", "combiners": ["max"],
         "source": {"kind": "window_sum", "days": 730, "column": "finish_loss"}},
    ],
}


def test_bout_row_static_and_order_free_combiners():
    ctx = {"weight_class": "Lightweight", "sched_rounds": 3.0}
    row = bout_row(_snap(), SPEC, "a", "b", "2026-11-01", ctx)
    assert row["elo_min"] == 1500.0 and row["elo_absdiff"] == 100.0
    assert row["stance_pair"] == "Orthodox|Southpaw"
    assert row == bout_row(_snap(), SPEC, "b", "a", "2026-11-01", ctx)


def test_bout_row_dated_recipes():
    row = bout_row(_snap(), SPEC, "a", "b", "2026-11-01", {})
    # max() with an unknown side is unknown (numpy semantics in the research build)
    assert math.isnan(row["age_max"])
    assert row["layoff_days_max"] == (pd.Timestamp("2026-11-01") - pd.Timestamp("2025-01-01")).days
    # window [2024-11-01, 2026-11-01): a's three bouts, b's none
    assert row["fights_2y_sum"] == 3.0
    assert row["finished_2y_max"] == 1.0  # nansum over the window
    later = bout_row(_snap(), SPEC, "a", "b", "2026-11-02", {})
    assert later["fights_2y_sum"] == 2.0 and later["finished_2y_max"] == 0.0


def test_age_bounds():
    snap = _snap()
    snap.loc["b", "dob"] = "1985-06-01"
    row = bout_row(snap, SPEC, "a", "b", "2026-11-01", {})
    assert row["age_max"] == pytest.approx((pd.Timestamp("2026-11-01") - pd.Timestamp("1985-06-01")).days / 365.25)
    snap.loc["b", "dob"] = "1900-01-01"
    assert math.isnan(bout_row(snap, SPEC, "a", "b", "2026-11-01", {})["age_max"])


def test_encode_categoricals_unknown_is_nan():
    rows = [bout_row(_snap(), SPEC, "a", "b", "2026-11-01", {"weight_class": wc, "sched_rounds": 5.0})
            for wc in ("Lightweight", "Strawweight", None)]
    A = encode(rows, SPEC)
    assert A.shape == (3, len(SPEC["columns"]))
    assert A[0, 0] == 1.0 and np.isnan(A[1, 0]) and np.isnan(A[2, 0])
    assert A[0, SPEC["columns"].index("stance_pair")] == 0.0
    assert (A[:, 1] == 5.0).all()


@pytest.mark.parametrize("loc,want", [
    ("Las Vegas, Nevada, USA", ("Las Vegas", "Nevada", "USA")),
    ("London, England, United Kingdom", ("London", "England", "United Kingdom")),
    ("Abu Dhabi, Abu Dhabi, United Arab Emirates", ("Abu Dhabi", "Abu Dhabi", "United Arab Emirates")),
    ("Singapore, Singapore", ("Singapore", None, "Singapore")),
    (None, (None, None, None)),
    ("", (None, None, None)),
])
def test_parse_location(loc, want):
    assert parse_location(loc) == want


def _event(**kw) -> Event:
    base = {"id": "e1", "slug": "s", "name": "UFC 999", "date": "2026-11-07", "status": "upcoming",
            "location": "Las Vegas, Nevada, USA", "fights": []}
    return Event(**{**base, **kw})


def test_bout_context():
    ev = _event()
    main = Fight(id="f1", red="a", blue="b", weight_class="Lightweight")
    title = Fight(id="f2", red="a", blue="b", weight_class="Women's Strawweight", title_fight=True)
    prelim = Fight(id="f3", red="a", blue="b", weight_class="Flyweight")
    c0, c1, c2 = bout_context(ev, main, 0), bout_context(ev, title, 1), bout_context(ev, prelim, 5)
    assert c0 == {"weight_class": "Lightweight", "gender": "M", "event_city": "Las Vegas", "event_state": "Nevada",
                  "event_country": "USA", "sched_rounds": 5.0, "title_fight": 0.0, "year": 2026.0}
    assert (c1["gender"], c1["sched_rounds"], c1["title_fight"]) == ("F", 5.0, 1.0)
    assert c2["sched_rounds"] == 3.0
    assert bout_context(ev, Fight(id="f4", red="a", blue="b"), 3)["gender"] is None


def test_tier_of_boundaries():
    t = {"low_max": 0.4, "medium_max": 0.6}
    assert [tier_of(p, t) for p in (0.1, 0.4, 0.41, 0.6, 0.61)] == ["low", "low", "medium", "medium", "high"]
