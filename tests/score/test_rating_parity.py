"""The rating roll-forward reproduces the research export's golden cases (fixtures/rating_cases.json).

Vendored from fight-auto-research `tests/fixtures/rating_cases.json`; schema in its `export/rating.py` docstring.
Each case starts from an empty population and must match the per-bout log and the final export rows to ~1e-9.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fightcard.score import elo
from fightcard.score.elo import Bout, Win, roll_forward

CASES = json.loads((Path(__file__).parent / "fixtures" / "rating_cases.json").read_text())
LOG_FLOATS = ("pre_a", "pre_b", "k_a", "k_b", "e_a", "w_a", "delta_a", "delta_b", "post_a", "post_b",
              "peak_a", "peak_b")


def close(got, want) -> bool:
    return got is None if want is None else got == pytest.approx(want, abs=1e-9)


def test_format_and_params_match_ours():
    p = CASES["params"]
    assert CASES["format_version"] == 1
    assert (elo.INIT, elo.DECAY_PER_YEAR, elo.DAYS_PER_YEAR) == (p["start"], p["decay_per_year"], p["days_per_year"])
    assert (elo.K_NUMERATOR, elo.K_OFFSET, elo.K_EXPONENT) == (p["k_numerator"], p["k_offset"], p["k_exponent"])
    assert elo.WIN_CREDIT == {"S-DEC": p["w_split"], "M-DEC": p["w_majority"]}
    assert set(elo.NO_UPDATE_METHODS) == set(p["no_update_methods"])
    assert elo.PEAK_UFC_BOUTS == p["peak_min_ufc_bouts"]
    d = p["display"]
    assert (elo.ESTABLISHED_N, elo.ANCHOR_DAYS, elo.ACTIVE_DAYS) == (
        d["anchor_min_bouts"], d["anchor_active_days"], d["active_days"])
    assert (elo.P4P_UFC_BOUTS, elo.P4P_SIZE, elo.PROVISIONAL_UFC_BOUTS) == (
        d["p4p_min_ufc_bouts"], {"M": d["p4p_men"], "F": d["p4p_women"]}, d["provisional_below_ufc_bouts"])


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["id"])
def test_golden_case(case):
    names = case["names"]
    bouts = [Bout(b["date"], b["fighter_a"], b["fighter_b"], b["result"], b["method"], ufc=b["ufc"],
                  weight_class=b["weight_class"], name_a=names[b["fighter_a"]], name_b=names[b["fighter_b"]])
             for b in case["bouts"]]
    states = {}
    steps = roll_forward(states, bouts)

    assert len(steps) == len(case["expected"]["bouts"])
    for step, want in zip(steps, case["expected"]["bouts"]):
        assert (step.bout.date, step.bout.a, step.bout.b) == (want["date"], want["fighter_a"], want["fighter_b"])
        for name in LOG_FLOATS:
            assert close(getattr(step, name), want[name]), f"{want['fight_id']} {name}: {getattr(step, name)} != {want[name]}"

    rows = case["expected"]["fighters"]
    assert sorted(states) == [r["fighter_id"] for r in rows]
    for r in rows:
        s = states[r["fighter_id"]]
        assert (s.n, s.n_ufc, s.last, s.gender) == (r["n_bouts"], r["n_ufc_bouts"], r["last_bout_date"], r["gender"])
        assert close(s.rating, r["rating"]) and close(s.peak, r["peak"]), r["fighter_id"]
        if r["best_win_opponent_id"] is None:
            assert s.best_win is None and r["best_win_opponent"] is r["best_win_rating"] is r["best_win_date"] is None
        else:
            assert s.best_win == Win(r["best_win_opponent_id"], r["best_win_opponent"],
                                     pytest.approx(r["best_win_rating"], abs=1e-9), r["best_win_date"])


def test_best_win_cases_are_present():
    assert {"best_win_split", "best_win_tie"} <= {c["id"] for c in CASES["cases"]}
