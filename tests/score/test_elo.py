"""Fighter rating (docs/ELO.md Contract): the research export rolled forward over results since `as_of`."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from fightcard.models import Fighter, PastFight, Record
from fightcard.score.elo import Bout, State, credit, decay, fighter_ratings, k_factor, roll_forward

AS_OF = "2026-10-14"
NOW = date(2026, 11, 1)
RECENT = "2026-10-01"  # 31 days before NOW
NULL = {"elo": None, "elo_peak": None, "p4p_rank": None, "elo_provisional": False, "elo_inactive": False}


def k(n: int) -> float:
    return 275 / (n + 5) ** 0.4


def e(ra: float, rb: float) -> float:
    return 1 / (1 + 10 ** ((rb - ra) / 400))


def dec(r: float, days: float) -> float:
    return 1500 + (r - 1500) * 0.9 ** (days / 365.25)


def export(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows).set_index("fighter_id")


def row(fid: str, rating: float, last: str = RECENT, n: int = 10, ufc: int = 10, peak: float | None = None,
        gender: str | None = "M") -> dict:
    return {"fighter_id": fid, "rating": rating, "last_bout_date": last, "n_bouts": n, "n_ufc_bouts": ufc,
            "peak": peak, "gender": gender}


def snap(*rows: tuple) -> pd.DataFrame:
    """Rows of (fighter_id, w, l, d, nc): the record as of the export."""
    cols = ["fighter_id", "record_w", "record_l", "record_d", "record_nc"]
    return pd.DataFrame(rows, columns=cols).set_index("fighter_id")


def past(opponent_id: str | None, result: str, date: str, method: str | None = "U-DEC",
         event: str = "UFC Fight Night") -> PastFight:
    return PastFight(result=result, opponent="X", opponent_id=opponent_id, method=method, event=event, date=date)


def fighter(fid: str, w=0, l=0, d=0, nc=0, fights: list[PastFight] | None = None) -> Fighter:
    return Fighter(id=fid, name=fid, record=Record(w=w, l=l, d=d, nc=nc), recent_fights=fights)


def rate(fighters: dict[str, Fighter], ratings: pd.DataFrame | None, records: dict[str, tuple] | None = None) -> dict:
    """fighter_ratings with each fighter's export-time record taken as (10, 0, 0, 0) unless given."""
    records = {fid: (10, 0, 0, 0) for fid in fighters} | (records or {})
    return fighter_ratings(fighters, ratings, snap(*[(f, *r) for f, r in records.items()]), AS_OF, NOW)


# --- formula pieces ---------------------------------------------------------------------------------------------

def test_k_shrinks_with_prior_bouts_and_has_no_floor():
    assert k_factor(0) == pytest.approx(144.46, abs=0.01)
    assert k_factor(20) == pytest.approx(275 / 25 ** 0.4)
    assert k_factor(500) == pytest.approx(275 / 505 ** 0.4)


@pytest.mark.parametrize("result,method,w", [
    ("win", "U-DEC", 1.0), ("win", "KO/TKO", 1.0), ("win", "SUB", 1.0), ("win", "DQ", 1.0), ("win", None, 1.0),
    ("win", "S-DEC", 0.6), ("win", "M-DEC", 0.8),
    ("loss", "U-DEC", 0.0), ("loss", "S-DEC", 0.4), ("loss", "M-DEC", 0.2),
    ("draw", "M-DEC", 0.5), ("draw", "S-DEC", 0.5),
    ("nc", "Overturned", None), ("nc", "CNC", None),
    ("win", "Overturned", None), ("loss", "Overturned", None), ("draw", "CNC", None),  # NC whatever the outcome
])
def test_credit_by_result_and_method(result, method, w):
    assert credit(result, method) == (pytest.approx(w) if w is not None else None)


def test_decay_is_ten_percent_a_year_toward_1500_and_continuous():
    assert decay(1700, "2025-11-01", "2026-11-01") == pytest.approx(dec(1700, 365))
    assert decay(1300, "2026-10-01", "2026-11-01") == pytest.approx(dec(1300, 31))
    assert decay(1700, "2026-11-01", "2026-11-01") == 1700
    assert decay(1700, None, "2026-11-01") == 1700


# --- roll-forward core: bouts in, per-bout steps out ------------------------------------------------------------

def test_one_bout_uses_each_sides_own_k_and_decays_to_the_day():
    states = {"a": State(rating=1600, n=10, n_ufc=10, last="2025-11-01"), "b": State(rating=1500, n=0)}
    [step] = roll_forward(states, [Bout("2026-11-01", "a", "b", "win")])
    ra, rb = dec(1600, 365), 1500
    assert (step.pre_a, step.pre_b) == (pytest.approx(ra), pytest.approx(rb))
    assert (step.k_a, step.k_b) == (pytest.approx(k(10)), pytest.approx(k(0)))
    assert step.delta_a == pytest.approx(k(10) * (1 - e(ra, rb)))
    assert step.delta_b == pytest.approx(k(0) * (0 - e(rb, ra)))
    assert states["a"].rating == pytest.approx(ra + step.delta_a)  # stored undecayed post-bout value
    assert step.post_a == pytest.approx(states["a"].rating)
    assert (states["a"].n, states["a"].n_ufc, states["a"].last) == (11, 11, "2026-11-01")
    assert (states["b"].n, states["b"].last) == (1, "2026-11-01")


def test_opponent_missing_from_states_starts_as_a_debutant():
    states = {"a": State(rating=1500, n=0)}
    [step] = roll_forward(states, [Bout("2026-10-24", "a", "new", "loss")])
    assert step.pre_b == 1500
    assert states["new"].rating == pytest.approx(1500 + k(0) * 0.5)
    assert states["new"].n == 1


def test_heavy_favourite_can_lose_points_on_a_split_win():
    states = {"a": State(rating=1700, n=20, last="2026-10-24"), "b": State(rating=1500, n=20, last="2026-10-24")}
    [step] = roll_forward(states, [Bout("2026-10-24", "a", "b", "win", "S-DEC")])
    assert step.delta_a == pytest.approx(k(20) * (0.6 - e(1700, 1500)))
    assert step.delta_a < 0 < step.delta_b


def test_no_contest_changes_no_rating_but_counts_toward_n_and_resets_the_clock():
    states = {"a": State(rating=1700, n=4, n_ufc=4, last="2025-10-24"), "b": State(rating=1500, n=4)}
    [step] = roll_forward(states, [Bout("2026-10-24", "a", "b", "nc", "Overturned")])
    assert step.delta_a == step.delta_b == 0
    assert states["a"].rating == pytest.approx(dec(1700, 365))
    assert (states["a"].n, states["a"].n_ufc, states["a"].last) == (5, 5, "2026-10-24")


def test_same_day_bouts_all_use_pre_day_ratings_and_n():
    # a beats b and c on one night (an old tournament): both bouts see a's pre-day 1500 and n=0.
    states = {f: State(rating=1500, n=0) for f in "abc"}
    steps = roll_forward(states, [Bout("2026-10-24", "a", "b", "win"), Bout("2026-10-24", "c", "a", "loss")])
    assert (steps[0].pre_a, steps[1].pre_b) == (1500, 1500)
    assert steps[0].delta_a == pytest.approx(k(0) * 0.5)
    assert steps[1].delta_b == pytest.approx(k(0) * 0.5)
    assert states["a"].rating == pytest.approx(1500 + 2 * k(0) * 0.5)
    assert states["a"].n == 2


def test_days_apply_in_date_order_whatever_the_input_order():
    later, first = Bout("2026-11-07", "a", "b", "win"), Bout("2026-10-24", "a", "b", "win")
    states = {"a": State(rating=1500, n=0), "b": State(rating=1500, n=0)}
    steps = roll_forward(states, [later, first])
    assert [s.bout for s in steps] == [first, later]
    a1, b1 = 1500 + k(0) * 0.5, 1500 - k(0) * 0.5
    a1d, b1d = dec(a1, 14), dec(b1, 14)
    assert steps[1].pre_a == pytest.approx(a1d)
    assert states["a"].rating == pytest.approx(a1d + k(1) * (1 - e(a1d, b1d)))


def test_peak_counts_from_the_fifth_ufc_bout_and_only_rises():
    states = {"a": State(rating=1500, n=3, n_ufc=3), "b": State(rating=1500, n=30, n_ufc=30)}
    roll_forward(states, [Bout("2026-01-01", "a", "b", "win")])  # 4th UFC bout
    assert states["a"].peak is None
    roll_forward(states, [Bout("2026-02-01", "a", "b", "win", ufc=False)])  # not UFC: still 4
    assert (states["a"].n, states["a"].n_ufc, states["a"].peak) == (5, 4, None)
    roll_forward(states, [Bout("2026-03-01", "a", "b", "win")])  # 5th UFC bout
    top = states["a"].rating
    assert states["a"].peak == pytest.approx(top)
    roll_forward(states, [Bout("2026-04-01", "a", "b", "loss")])
    assert states["a"].peak == pytest.approx(top)
    states["c"] = State(rating=2200, n=30, n_ufc=30, last="2026-05-01")
    roll_forward(states, [Bout("2026-05-01", "a", "c", "win", ufc=False)])  # any bout counts from then on
    assert states["a"].peak == pytest.approx(states["a"].rating) and states["a"].peak > top


# --- fighter_ratings: export + card fighters' newer results -> Fighter fields -----------------------------------

def test_without_a_rating_export_every_fighter_is_unrated():
    fighters = {"a": fighter("a", w=4, fights=[past("b", "win", "2026-10-24")])}
    assert rate(fighters, None, {"a": (3, 0, 0, 0)}) == {"a": NULL}


def test_no_new_fights_shows_the_anchored_decayed_rating():
    ratings = export(row("a", 1600), row("b", 1400))  # decayed mean is exactly 1500: no shift
    assert rate({"a": fighter("a", w=10)}, ratings)["a"]["elo"] == round(dec(1600, 31))


def test_anchor_puts_the_mean_of_active_established_fighters_at_1500():
    ratings = export(row("a", 1700), row("b", 1500),
                     row("rookie", 1900, n=2, ufc=2),          # n < 3: not in the mean
                     row("gone", 1900, last="2024-01-01"))     # no bout in 730 days: not in the mean
    shift = 1500 - (dec(1700, 31) + 1500) / 2
    out = rate({f: fighter(f, w=10) for f in ratings.index}, ratings)
    assert out["a"]["elo"] == round(dec(1700, 31) + shift)
    assert out["b"]["elo"] == round(1500 + shift)
    assert out["rookie"]["elo"] == round(dec(1900, 31) + shift)
    assert out["gone"]["elo"] == round(dec(1900, (NOW - date(2024, 1, 1)).days) + shift)


def test_peak_is_shown_on_the_anchored_scale_undecayed():
    ratings = export(row("a", 1700, peak=1800), row("b", 1300, peak=None))
    out = rate({"a": fighter("a", w=10), "b": fighter("b", w=10)}, ratings)
    assert out["a"]["elo_peak"] == 1800  # decayed mean is 1500: no shift
    assert out["b"]["elo_peak"] is None


def test_tags_provisional_under_three_ufc_bouts_and_inactive_after_a_year():
    ratings = export(row("new", 1500, n=8, ufc=2), row("vet", 1500, ufc=3),
                     row("idle", 1500, last="2025-10-31"), row("active", 1500, last="2025-11-01"))
    out = rate({f: fighter(f, w=10) for f in ratings.index}, ratings)
    assert (out["new"]["elo_provisional"], out["vet"]["elo_provisional"]) == (True, False)
    assert (out["idle"]["elo_inactive"], out["active"]["elo_inactive"]) == (True, False)


def test_p4p_ranks_top_15_men_and_top_10_women_among_eligible():
    men = [row(f"m{i:02}", 1800 - 10 * i) for i in range(20)]
    women = [row(f"w{i:02}", 1700 - 10 * i, gender="F") for i in range(12)]
    ineligible = [row("few", 2000, ufc=4), row("idle", 2000, last="2025-10-01"), row("nog", 2000, gender=None)]
    ratings = export(*men, *women, *ineligible)
    out = rate({f: fighter(f, w=10) for f in ratings.index}, ratings)
    assert [out[f"m{i:02}"]["p4p_rank"] for i in range(16)] == [*range(1, 16), None]
    assert [out[f"w{i:02}"]["p4p_rank"] for i in range(11)] == [*range(1, 11), None]
    assert out["few"]["p4p_rank"] is out["idle"]["p4p_rank"] is out["nog"]["p4p_rank"] is None


def test_ranking_covers_the_whole_export_not_just_the_card():
    ratings = export(row("champ", 1900), row("a", 1700), row("b", 1500))
    assert rate({"a": fighter("a", w=10)}, ratings)["a"]["p4p_rank"] == 2


def test_a_card_fighters_newer_win_moves_both_sides_once():
    ratings = export(row("a", 1500, last="2026-10-24"), row("b", 1500, last="2026-10-24"))
    fighters = {
        "a": fighter("a", w=4, l=2, fights=[past("b", "win", "2026-10-31")]),
        "b": fighter("b", w=3, l=3, fights=[past("a", "loss", "2026-10-31")]),
    }
    out = rate(fighters, ratings, {"a": (3, 2, 0, 0), "b": (3, 2, 0, 0)})
    gain = k(10) * 0.5
    assert out["a"]["elo"] == round(dec(1500 + gain, 1))  # mean stays 1500: no shift
    assert out["b"]["elo"] == round(dec(1500 - gain, 1))


def test_fights_on_or_before_as_of_are_already_in_the_export():
    ratings = export(row("a", 1600), row("b", 1400))
    fighters = {"a": fighter("a", w=4, fights=[past("b", "win", AS_OF), past("b", "win", "2026-09-01")])}
    assert rate(fighters, ratings, {"a": (4, 0, 0, 0)})["a"]["elo"] == round(dec(1600, 31))


def test_split_decision_from_the_method_and_debutant_opponent():
    ratings = export(row("a", 1600, last="2026-10-31"), row("b", 1400, last="2026-10-31"))
    fighters = {"a": fighter("a", w=4, fights=[past("new", "win", "2026-10-31", method="S-DEC")])}
    out = rate(fighters, ratings, {"a": (3, 0, 0, 0)})
    new_a = 1600 + k(10) * (0.6 - e(1600, 1500))
    # "new" has n=1 after the bout: not established, so the mean is a and b (both 1 day since their last bout)
    shift = 1500 - (dec(new_a, 1) + dec(1400, 1)) / 2
    assert out["a"]["elo"] == round(dec(new_a, 1) + shift)


def test_newer_ufc_bouts_count_toward_tags_and_peak():
    ratings = export(row("a", 1500, n=10, ufc=4, last="2026-10-24"), row("b", 1500, last="2026-10-24"))
    fighters = {"a": fighter("a", w=5, fights=[past("b", "win", "2026-10-31", event="UFC 333")])}
    out = rate(fighters, ratings, {"a": (4, 0, 0, 0)})
    assert out["a"]["elo_peak"] == round(1500 + k(10) * 0.5)  # 5th UFC bout; mean stays 1500
    assert out["a"]["p4p_rank"] == 1


@pytest.mark.parametrize("event", ["DWCS 10.6", "Road to UFC 5.1", "Road To UFC 5.2", "ROAD TO UFC 5.3", "dwcs 10.7"])
def test_contender_series_and_road_to_ufc_are_not_ufc_bouts_in_any_case(event):
    ratings = export(row("a", 1500, n=10, ufc=2, last="2026-10-24"), row("b", 1500, last="2026-10-24"))
    fights = [past("b", "win", "2026-10-31", event=event)]
    out = rate({"a": fighter("a", w=5, fights=fights)}, ratings, {"a": (4, 0, 0, 0)})
    assert out["a"]["elo_provisional"] is True


def test_missing_newer_fights_nulls_the_fighter_and_drops_them_from_the_ranking():
    ratings = export(row("a", 1700), row("b", 1600), row("c", 1500))
    fighters = {"a": fighter("a", w=5, fights=[past("c", "win", "2026-10-24")]),  # record says 2 since
                "b": fighter("b", w=10)}
    out = rate(fighters, ratings, {"a": (3, 0, 0, 0)})
    assert out["a"] == NULL
    assert out["b"]["p4p_rank"] == 1


def test_newer_fight_without_opponent_id_nulls_the_fighter():
    fighters = {"a": fighter("a", w=4, fights=[past(None, "win", "2026-10-24")])}
    assert rate(fighters, export(row("a", 1500)), {"a": (3, 0, 0, 0)}) == {"a": NULL}


def test_not_in_the_export_and_no_newer_bouts_is_unrated():
    out = rate({"rookie": fighter("rookie", w=10), "nobody": fighter("nobody", w=3)}, export(row("b", 1500)))
    assert out == {"rookie": NULL, "nobody": NULL}


def test_debut_since_the_export_starts_at_1500():
    ratings = export(row("b", 1500, last="2026-10-31"))
    fighters = {"new": fighter("new", w=1, fights=[past("b", "win", "2026-10-31")])}
    out = rate(fighters, ratings, {"new": (0, 0, 0, 0)})
    new, b = 1500 + k(0) * 0.5, 1500 - k(10) * 0.5
    assert out["new"]["elo"] == round(dec(new, 1) + 1500 - dec(b, 1))  # only b is established
    assert out["new"]["elo_provisional"] is True
