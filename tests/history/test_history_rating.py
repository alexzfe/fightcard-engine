"""The fighter rating's chronological pass (`fightcard.history.rating`, ported from fight-auto-research
`main:export/rating.py`): formula pieces, the golden cases, and parity with research's last export (`-m seed`)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from history_helpers import SEED_CSV, read_csv

from fightcard.history import display, rating
from fightcard.history.rating import (
    COLUMNS,
    PARAMS,
    decayed,
    expected,
    golden_cases,
    history_ratings,
    k_factor,
    rate,
    records,
    result_score,
)
from fightcard.history.store import connect, upsert_event
from fightcard.models import Event, Fight, Result

REPO = Path(__file__).resolve().parents[2]
CASES_PATH = REPO / "tests" / "score" / "fixtures" / "rating_cases.json"
CASES = json.loads(CASES_PATH.read_text())
LOG_FLOATS = ("pre_a", "pre_b", "k_a", "k_b", "e_a", "w_a", "delta_a", "delta_b", "post_a", "post_b",
              "peak_a", "peak_b")


def k(n: int) -> float:
    return 275 / (n + 5) ** 0.4


def e(ra: float, rb: float) -> float:
    return 1 / (1 + 10 ** ((rb - ra) / 400))


def dec(r: float, days: float) -> float:
    return 1500 + (r - 1500) * 0.9 ** (days / 365.25)


def bout(day, fid, a, b, result="win", method="KO/TKO", ufc=True, wc="Lightweight") -> dict:
    return {"date": day, "fight_id": fid, "fighter_a": a, "fighter_b": b, "result": result, "method": method,
            "ufc": ufc, "weight_class": wc}


def close(got, want) -> bool:
    return (got is None or pd.isna(got)) if want is None else got == pytest.approx(want, abs=1e-9)


# --- formula pieces -------------------------------------------------------------------------------------------

def test_k_shrinks_with_prior_bouts_and_has_no_floor():
    assert k_factor(0) == pytest.approx(144.46, abs=0.01)
    assert k_factor(20) == pytest.approx(275 / 25 ** 0.4)
    assert k_factor(500) == pytest.approx(275 / 505 ** 0.4)


def test_expected_score():
    assert expected(1500, 1500) == 0.5
    assert expected(1700, 1500) == pytest.approx(e(1700, 1500))


@pytest.mark.parametrize("result,method,w", [
    ("win", "U-DEC", 1.0), ("win", "KO/TKO", 1.0), ("win", "SUB", 1.0), ("win", "DQ", 1.0), ("win", None, 1.0),
    ("win", "Decision", 1.0), ("win", "S-DEC", 0.6), ("win", "M-DEC", 0.8),
    ("loss", "U-DEC", 0.0), ("loss", "S-DEC", 0.4), ("loss", "M-DEC", 0.2),
    ("draw", "M-DEC", 0.5), ("draw", "S-DEC", 0.5),
    ("nc", "Overturned", None), ("nc", "CNC", None),
    ("win", "Overturned", None), ("loss", "Overturned", None), ("draw", "CNC", None),  # NC whatever the outcome
])
def test_result_score_by_result_and_method(result, method, w):
    assert result_score(result, method) == (pytest.approx(w) if w is not None else None)


def test_result_score_rejects_an_unknown_result():
    with pytest.raises(ValueError, match="unknown result"):
        result_score("dq", "DQ")


def test_decay_is_ten_percent_a_year_toward_1500_and_continuous():
    assert decayed(1700, 365) == pytest.approx(dec(1700, 365))
    assert decayed(1300, 31) == pytest.approx(dec(1300, 31))
    assert decayed(1700, 0) == 1700


# --- the chronological pass -----------------------------------------------------------------------------------

def test_one_bout_uses_each_sides_own_k_and_decays_to_the_day():
    log, rows = rate([bout("2025-11-01", "f0", "a", "c")] + [bout("2026-11-01", "f1", "a", "b")])
    step = log[1]
    ra = dec(1500 + k(0) / 2, 365)
    assert (step["pre_a"], step["pre_b"]) == (pytest.approx(ra), 1500)
    assert (step["k_a"], step["k_b"]) == (pytest.approx(k(1)), pytest.approx(k(0)))
    assert step["delta_a"] == pytest.approx(k(1) * (1 - e(ra, 1500)))
    a = rows.set_index("fighter_id").loc["a"]
    assert a["rating"] == pytest.approx(ra + step["delta_a"])  # stored undecayed post-bout value
    assert (a["n_bouts"], a["n_ufc_bouts"], a["last_bout_date"]) == (2, 2, "2026-11-01")


def test_heavy_favourite_can_lose_points_on_a_split_win():
    log, _ = rate([bout("2026-01-01", "f1", "a", "x"), bout("2026-01-02", "f2", "a", "y"),
                   bout("2026-01-03", "f3", "a", "b", method="S-DEC")])
    assert log[2]["delta_a"] < 0 < log[2]["delta_b"]


def test_no_contest_changes_no_rating_but_counts_toward_n_and_resets_the_clock():
    log, rows = rate([bout("2025-01-01", "f1", "a", "b"), bout("2026-01-01", "f2", "a", "b", "nc", "Overturned")])
    assert log[1]["delta_a"] == log[1]["delta_b"] == 0 and log[1]["w_a"] is None
    a = rows.set_index("fighter_id").loc["a"]
    assert a["rating"] == pytest.approx(dec(1500 + k(0) / 2, 365))
    assert (a["n_bouts"], a["last_bout_date"]) == (2, "2026-01-01")


def test_same_day_bouts_all_use_pre_day_ratings_and_n():
    log, rows = rate([bout("2026-10-24", "f1", "a", "b"), bout("2026-10-24", "f2", "c", "a", "loss")])
    assert (log[0]["pre_a"], log[1]["pre_b"]) == (1500, 1500)
    a = rows.set_index("fighter_id").loc["a"]
    assert a["rating"] == pytest.approx(1500 + 2 * k(0) * 0.5)
    assert a["n_bouts"] == 2


def test_days_apply_in_date_order_whatever_the_input_order():
    log, _ = rate([bout("2026-11-07", "f2", "a", "b"), bout("2026-10-24", "f1", "a", "b")])
    assert [s["fight_id"] for s in log] == ["f1", "f2"]
    assert log[1]["pre_a"] == pytest.approx(dec(1500 + k(0) / 2, 14))


def test_peak_counts_from_the_fifth_ufc_bout_and_non_ufc_bouts_count_toward_n_only():
    bouts = [bout(f"2026-0{i}-01", f"f{i}", "a", f"o{i}", ufc=i != 2) for i in range(1, 7)]
    log, rows = rate(bouts)
    assert [s["peak_a"] for s in log[:5]] == [None] * 5  # bout 2 isn't UFC: the 5th UFC bout is the 6th bout
    assert log[5]["peak_a"] == pytest.approx(log[5]["post_a"])
    a = rows.set_index("fighter_id").loc["a"]
    assert (a["n_bouts"], a["n_ufc_bouts"]) == (6, 5)


@pytest.mark.parametrize("classes, gender", [(["Women's Flyweight", "Catch Weight"], "F"),
                                             (["Lightweight", None], "M"),
                                             (["Super Heavyweight"], "M"),
                                             (["Catch Weight", "Open Weight", None], None)])
def test_gender_from_weight_classes(classes, gender):
    _, rows = rate([bout(f"2026-01-0{i + 1}", f"f{i}", "a", f"o{i}", wc=wc) for i, wc in enumerate(classes)])
    assert rows.set_index("fighter_id").loc["a", "gender"] == gender


def test_best_win_is_the_opponent_rated_highest_going_in_named_from_names():
    log, rows = rate([bout("2025-01-01", "f1", "s", "x"), bout("2026-01-01", "f2", "a", "w"),
                      bout("2026-01-01", "f3", "s", "a", "loss", "S-DEC")], {"s": "Strong"})
    a = rows.set_index("fighter_id").loc["a"]
    assert (a["best_win_opponent_id"], a["best_win_opponent"], a["best_win_date"]) == ("s", "Strong", "2026-01-01")
    assert a["best_win_rating"] == pytest.approx(dec(1500 + k(0) / 2, 365))


def test_export_rows_have_the_parquet_columns_sorted_by_id():
    _, rows = rate([bout("2026-01-01", "f1", "b", "a")])
    assert list(rows.columns) == COLUMNS
    assert list(rows["fighter_id"]) == ["a", "b"]
    assert records(rows)[0]["peak"] is None


# --- golden cases (tests/score/fixtures/rating_cases.json) ------------------------------------------------------

def test_params_match_the_golden_cases_and_the_display_layer():
    assert CASES["format_version"] == rating.FORMAT_VERSION == 1
    assert CASES["params"] == PARAMS
    d = PARAMS["display"]
    assert (display.ESTABLISHED_N, display.ANCHOR_DAYS, display.ACTIVE_DAYS) == (
        d["anchor_min_bouts"], d["anchor_active_days"], d["active_days"])
    assert (display.P4P_UFC_BOUTS, display.P4P_SIZE, display.PROVISIONAL_UFC_BOUTS) == (
        d["p4p_min_ufc_bouts"], {"M": d["p4p_men"], "F": d["p4p_women"]}, d["provisional_below_ufc_bouts"])


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["id"])
def test_golden_case(case):
    log, rows = rate(case["bouts"], case["names"])
    want = case["expected"]
    assert len(log) == len(want["bouts"])
    for got, row in zip(log, want["bouts"]):
        assert (got["fight_id"], got["date"], got["fighter_a"], got["fighter_b"]) == (
            row["fight_id"], row["date"], row["fighter_a"], row["fighter_b"])
        for name in LOG_FLOATS:
            assert close(got[name], row[name]), f"{row['fight_id']} {name}: {got[name]} != {row[name]}"
    got_rows = records(rows)
    assert [r["fighter_id"] for r in got_rows] == [r["fighter_id"] for r in want["fighters"]]
    for got, row in zip(got_rows, want["fighters"]):
        for col in COLUMNS:
            if isinstance(row[col], float):
                assert close(got[col], row[col]), f"{row['fighter_id']} {col}"
            else:
                assert got[col] == row[col], f"{row['fighter_id']} {col}"


def test_generator_reproduces_the_golden_case_file_byte_for_byte(tmp_path):
    out = tmp_path / "rating_cases.json"
    done = subprocess.run([sys.executable, "-m", "fightcard.history.rating", str(out)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert out.read_bytes() == CASES_PATH.read_bytes()
    assert golden_cases()["cases"][0]["id"] == "debut"


# --- fed by the history store ---------------------------------------------------------------------------------

def test_history_ratings_rate_the_store_up_to_as_of(tmp_path):
    conn = connect(tmp_path / "h.sqlite")
    fights = [Fight(id="f1", red="b", blue="a", weight_class="Lightweight",
                    result=Result(winner="b", outcome="win", method="S-DEC"))]
    with conn:
        upsert_event(conn, Event(id="e1", slug="x", name="UFC 1", date="2026-01-01", status="completed",
                                 fights=fights), is_ufc=True, source="listing")
        upsert_event(conn, Event(id="e2", slug="y", name="UFC 2", date="2026-02-01", status="completed",
                                 fights=[fights[0].model_copy(update={"id": "f2"})]), is_ufc=True, source="listing")
        conn.execute("INSERT INTO fighters (fighter_id, name) VALUES ('a', 'Al'), ('b', 'Bo')")
    got = history_ratings(conn, as_of="2026-01-31")
    assert list(got.index) == ["a", "b"] and got.index.name == "fighter_id"
    assert got.loc["b", "rating"] == pytest.approx(1500 + k(0) * (0.6 - 0.5))
    assert got.loc["b", "best_win_opponent"] == "Al"
    assert history_ratings(conn).loc["b", "n_bouts"] == 2
    conn.close()


@pytest.mark.seed
def test_full_history_pass_reproduces_research_export(seed_db):
    """Restricted to research's own bouts (the store also holds 14 old PRIDE / Strikeforce bouts research's
    CSVs lack, see test_history_seed.py) and to bouts up to the export's as_of."""
    model = REPO / "model"
    as_of = json.loads((model / "manifest.json").read_text())["as_of"]
    want = pd.read_parquet(model / "fighter_rating.parquet").set_index("fighter_id")
    research = {r["fight_id"] for r in read_csv(SEED_CSV / "fight_data.csv")}
    from fightcard.history.store import names, rating_bouts
    bouts = [b for b in rating_bouts(seed_db, as_of=as_of) if b["fight_id"] in research]
    got = rate(bouts, names(seed_db))[1].set_index("fighter_id")

    assert len(want) == 4_146 and list(got.index) == list(want.index)
    for col in ("rating", "peak", "best_win_rating"):
        assert (got[col].isna() == want[col].isna()).all(), col
        assert (got[col] - want[col]).abs().max() <= 1e-9, col
    for col in ("last_bout_date", "n_bouts", "n_ufc_bouts", "gender", "best_win_opponent_id", "best_win_date"):
        same = (got[col] == want[col]) | (got[col].isna() & want[col].isna())
        assert same.all(), (col, list(got.index[~same][:5]))
    # Names: ours come from the fighter page; research's fighter_data.csv splits this one as just "Tiago".
    same = (got["best_win_opponent"] == want["best_win_opponent"]) | want["best_win_opponent"].isna()
    assert list(got.index[~same]) == ["1971869b7e054261"]
