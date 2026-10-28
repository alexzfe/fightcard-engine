"""Ratings as of any date (`fightcard.history.timeline`), built from the one history pass's per-bout log.

The key guarantee is equivalence: the timeline as of going into a day D gives exactly what a fresh pass over the
bouts strictly before D (`rate` / `history_pass(as_of=D-1)`) plus the existing P4P code gives. It runs on synthetic
bouts and on the frozen parity bouts (always), and on the full store (`-m seed`)."""

from __future__ import annotations

import json
import random
import time
from datetime import date, timedelta

import pandas as pd
import pytest
from history_helpers import PARITY

from fightcard.history.rankings import divisions, p4p
from fightcard.history.rating import START, decayed, history_pass, rate
from fightcard.history.store import connect, upsert_event
from fightcard.history.timeline import Side, State, Timeline
from fightcard.models import Event, Fight, Result

FROZEN = json.loads(PARITY.read_text(encoding="utf-8"))
COLUMNS = ["rating", "last_bout_date", "n_bouts", "n_ufc_bouts", "peak", "gender"]


def bout(day, fid, a, b, result="win", method="KO/TKO", ufc=True, wc="Lightweight") -> dict:
    return {"date": day, "fight_id": fid, "fighter_a": a, "fighter_b": b, "result": result, "method": method,
            "ufc": ufc, "weight_class": wc}


def eve(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def fresh(bouts: list[dict], names: dict, day: str) -> tuple[pd.DataFrame, list[dict]]:
    """A fresh pass over the bouts strictly before `day`: (export rows by fighter_id, those bouts)."""
    before = [b for b in bouts if b["date"] <= eve(day)]
    return rate(before, names)[1].set_index("fighter_id"), before


def assert_equivalent(tl: Timeline, want: pd.DataFrame, before: list[dict], names: dict, day: str) -> None:
    got = tl.ratings(day)
    assert list(got.index) == list(want.index)
    if len(want):
        pd.testing.assert_frame_equal(got[COLUMNS], want[COLUMNS])
    today = date.fromisoformat(day)
    for fid, r in want.iterrows():
        peak = None if pd.isna(r["peak"]) else float(r["peak"])
        assert tl.state(fid, day) == State(
            rating=decayed(float(r["rating"]), (today - date.fromisoformat(r["last_bout_date"])).days),
            n_bouts=int(r["n_bouts"]), n_ufc_bouts=int(r["n_ufc_bouts"]), last_bout_date=r["last_bout_date"],
            gender=r["gender"], peak=peak), fid
    assert tl.p4p(day) == p4p(want, names, divisions(before), date.fromisoformat(eve(day)))


def league(seed: int = 0) -> tuple[list[dict], dict]:
    """A synthetic population over three years: 12 men and 8 women fighting within their gender every few weeks,
    some non-UFC cards, splits, draws and NCs, and an early-UFC-style tournament day (2020-06-06) where m0 fights
    four times."""
    rnd = random.Random(seed)
    men, women = [f"m{i}" for i in range(12)], [f"w{i}" for i in range(8)]
    bouts, day, i = [], date(2019, 1, 5), 0
    while day < date(2022, 1, 1):
        for pool, wc in ((men, "Lightweight"), (women, "Women's Strawweight")):
            for _ in range(2):
                a, b = rnd.sample(pool, 2)
                result, method = rnd.choice([("win", "KO/TKO"), ("win", "S-DEC"), ("loss", "M-DEC"),
                                             ("draw", "U-DEC"), ("nc", None), ("win", "SUB"), ("loss", "U-DEC")])
                bouts.append(bout(day.isoformat(), f"x{i:04d}", a, b, result, method, ufc=rnd.random() > 0.15,
                                  wc=rnd.choice([wc, wc, "Catch Weight"])))
                i += 1
        day += timedelta(days=rnd.randint(10, 40))
    bouts = [b for b in bouts if b["date"] != "2020-06-06"]
    bouts += [bout("2020-06-06", "t1", "m0", "m1"), bout("2020-06-06", "t2", "m2", "m3", "loss"),
              bout("2020-06-06", "t3", "m0", "m3", method="S-DEC"), bout("2020-06-06", "t4", "m4", "m0", "draw"),
              bout("2020-06-06", "t5", "m0", "m5", "loss", "SUB")]
    names = {f: f"Name {f}" for f in men + women}
    return bouts, names


def timeline(bouts: list[dict], names: dict) -> Timeline:
    return Timeline(rate(bouts, names)[0], bouts, names)


# --- equivalence (always runs) --------------------------------------------------------------------------------

LEAGUE_DAYS = ["2019-01-01", "2019-01-05", "2019-01-06", "2020-06-06", "2020-06-07", "2020-06-08", "2021-03-03",
               "2021-12-31", "2022-06-01", "2024-01-01"]


@pytest.mark.parametrize("day", LEAGUE_DAYS)
def test_timeline_equals_a_fresh_pass_before_the_day_on_a_synthetic_league(day):
    bouts, names = league()
    want, before = fresh(bouts, names, day)
    assert_equivalent(timeline(bouts, names), want, before, names, day)


def test_the_synthetic_league_has_p4p_lists_to_compare():
    bouts, names = league()
    lists = timeline(bouts, names).p4p("2021-06-01")
    assert len(lists["men"]) >= 5 and len(lists["women"]) >= 5


FROZEN_DAYS = ["1993-11-12", "1993-11-13", "1994-03-11", "1994-03-12", "2005-07-04", "2015-01-01", "2026-09-15",
               "2026-09-16"]


@pytest.mark.parametrize("day", FROZEN_DAYS)
def test_timeline_equals_a_fresh_pass_before_the_day_on_the_frozen_parity_bouts(day):
    """The frozen parity bouts (tests/fixtures/history/rating_parity.json) hold UFC 1 (1993-11-12, a tournament day)
    and run to 2026-09-15."""
    bouts, names = FROZEN["bouts"], FROZEN["names"]
    want, before = fresh(bouts, names, day)
    assert_equivalent(timeline(bouts, names), want, before, names, day)


def test_before_any_bout_nobody_is_rated():
    bouts, names = league()
    tl = timeline(bouts, names)
    assert tl.state("m0", "2019-01-05") is None  # the first card's day: nothing strictly before it
    assert tl.state("nobody", "2030-01-01") is None
    assert tl.ratings("2019-01-05").empty
    assert tl.p4p("2019-01-05") == {"men": [], "women": []}


def test_the_state_is_decayed_to_the_day_going_in():
    tl = timeline([bout("2020-01-01", "f1", "a", "b"), bout("2023-01-01", "f2", "a", "c")], {})
    first = tl.bout("f1")["a"]
    going_in = tl.state("a", "2023-01-01")
    assert going_in.rating == decayed(START + first.delta, 1096)
    assert tl.bout("f2")["a"].pre == going_in.rating
    assert going_in.last_bout_date == "2020-01-01" and going_in.n_bouts == 1


# --- per bout ---------------------------------------------------------------------------------------------------

def test_bout_gives_each_fighters_pre_and_own_delta_from_the_log():
    bouts, names = league()
    log = rate(bouts, names)[0]
    tl = Timeline(log, bouts, names)
    for row in log:
        assert tl.bout(row["fight_id"]) == {row["fighter_a"]: Side(row["pre_a"], row["delta_a"]),
                                            row["fighter_b"]: Side(row["pre_b"], row["delta_b"])}
        for side in ("a", "b"):
            s = tl.state(row[f"fighter_{side}"], row["date"])
            assert row[f"pre_{side}"] == (START if s is None else s.rating)
    assert tl.bout("no-such-fight") is None


def test_a_tournament_day_gives_each_bout_its_own_delta_summing_to_the_days_change():
    """m0 fights four times on 2020-06-06 (t1 win, t3 split win, t4 draw as fighter_b, t5 loss): every bout starts
    from the same pre-day rating, each delta is that bout's own, and they sum to post - pre."""
    bouts, names = league()
    tl = timeline(bouts, names)
    day = [r for r in tl.series("m0") if r["date"] == "2020-06-06"]
    assert [r["fight_id"] for r in day] == ["t1", "t3", "t4", "t5"]
    pre = tl.state("m0", "2020-06-06").rating
    deltas = [tl.bout(r["fight_id"])["m0"].delta for r in day]
    assert all(tl.bout(r["fight_id"])["m0"].pre == pre for r in day)
    assert len(set(deltas)) == 4 and deltas[0] > 0 > deltas[3]
    after = tl.ratings("2020-06-07").loc["m0", "rating"]
    assert sum(deltas) == pytest.approx(after - pre, abs=1e-9)
    assert all(r["post"] == after for r in day)  # post is the end-of-day value, the same on every bout of the day
    assert tl.state("m0", "2020-06-07").n_bouts == tl.state("m0", "2020-06-06").n_bouts + 4


def test_no_update_bouts_have_a_zero_delta():
    tl = timeline([bout("2020-01-01", "f1", "a", "b"), bout("2020-03-01", "f2", "a", "b", "nc", None),
                   bout("2020-05-01", "f3", "a", "b", "draw", "CNC"),
                   bout("2020-07-01", "f4", "b", "a", "win", "Overturned")], {})
    for fid in ("f2", "f3", "f4"):
        assert {s.delta for s in tl.bout(fid).values()} == {0.0}


def test_series_is_the_fighters_bouts_in_date_order():
    tl = timeline([bout("2020-03-01", "f2", "c", "a", "loss"), bout("2020-01-01", "f1", "a", "b"),
                   bout("2020-05-01", "f3", "b", "c")], {})
    got = tl.series("a")
    assert [(r["date"], r["fight_id"], r["opponent"]) for r in got] == [("2020-01-01", "f1", "b"),
                                                                        ("2020-03-01", "f2", "c")]
    assert got[1]["pre"] == tl.bout("f2")["a"].pre and got[1]["delta"] == tl.bout("f2")["a"].delta > 0
    assert got[1]["post"] == got[1]["pre"] + got[1]["delta"]
    assert tl.series("nobody") == []


# --- the one pass -----------------------------------------------------------------------------------------------

def test_history_pass_keeps_rates_log_and_the_timeline_builds_from_it(tmp_path):
    conn = connect(tmp_path / "h.sqlite")
    fights = [Fight(id="f1", red="b", blue="a", weight_class="Lightweight",
                    result=Result(winner="b", outcome="win", method="S-DEC"))]
    with conn:
        upsert_event(conn, Event(id="e1", slug="x", name="UFC 1", date="2026-01-01", status="completed",
                                 fights=fights), is_ufc=True, source="listing")
        upsert_event(conn, Event(id="e2", slug="y", name="UFC 2", date="2026-02-01", status="completed",
                                 fights=[fights[0].model_copy(update={"id": "f2"})]), is_ufc=True, source="listing")
    hp = history_pass(conn)
    assert hp.log == rate(hp.bouts, hp.names)[0] and [r["fight_id"] for r in hp.log] == ["f1", "f2"]
    tl = Timeline.from_pass(hp)
    assert tl.bout("f2")["b"].pre == tl.state("b", "2026-02-01").rating
    conn.close()


# --- the full store (-m seed) -------------------------------------------------------------------------------------

@pytest.mark.seed
def test_timeline_equals_history_pass_as_of_the_day_before_on_the_full_store(seed_db):
    """UFC 1 (a tournament day) and the day after, a date with no bouts, a recent card and the day after the last
    bout: `history_pass(as_of=D-1)` plus the P4P code, against the timeline built from the one full pass."""
    started = time.perf_counter()
    hp = history_pass(seed_db)
    passed = time.perf_counter()
    tl = Timeline.from_pass(hp)
    built = time.perf_counter()
    days = sorted({b["date"] for b in hp.bouts})
    quiet, after = "2010-12-25", (date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat()
    assert quiet not in days and "1993-11-12" in days
    for day in ("1993-11-12", "1993-11-13", quiet, days[-5], days[-1], after):
        want = history_pass(seed_db, eve(day))
        assert_equivalent(tl, want.ratings, want.bouts, want.names, day)
    pd.testing.assert_frame_equal(tl.ratings(after)[COLUMNS], hp.ratings[COLUMNS])
    print(f"history_pass {passed - started:.2f} s, timeline build {built - passed:.2f} s, {len(hp.log)} bouts")
