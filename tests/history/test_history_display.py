"""The displayed rating (docs/ELO.md Display): the full-history pass's rows decayed to now, anchored, ranked and
tagged (`fightcard.history.display.fighter_ratings`)."""

from __future__ import annotations

from datetime import date

import pandas as pd

from fightcard.history.display import fighter_ratings

NOW = date(2026, 11, 1)
RECENT = "2026-10-01"  # 31 days before NOW
NULL = {"elo": None, "elo_peak": None, "p4p_rank": None, "elo_provisional": False, "elo_inactive": False,
        "best_win": None}


def dec(r: float, days: float) -> float:
    return 1500 + (r - 1500) * 0.9 ** (days / 365.25)


def ratings(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows).set_index("fighter_id")


def row(fid: str, rating: float, last: str = RECENT, n: int = 10, ufc: int = 10, peak: float | None = None,
        gender: str | None = "M", best: tuple | None = None) -> dict:
    """`best` = (opponent_id, opponent, rating, date); null best_win_* otherwise."""
    best = best or (None, None, None, None)
    return {"fighter_id": fid, "rating": rating, "last_bout_date": last, "n_bouts": n, "n_ufc_bouts": ufc,
            "peak": peak, "gender": gender,
            **dict(zip(("best_win_opponent_id", "best_win_opponent", "best_win_rating", "best_win_date"), best))}


def show(rows: pd.DataFrame | None, ids=None) -> dict:
    return fighter_ratings(list(rows.index) if ids is None else ids, rows, NOW)


def test_without_a_history_every_fighter_is_unrated():
    assert fighter_ratings(["a"], None, NOW) == {"a": NULL}


def test_a_fighter_without_bouts_in_scope_is_unrated():
    assert show(ratings(row("b", 1500)), ["rookie"]) == {"rookie": NULL}


def test_shows_the_anchored_rating_decayed_to_now():
    rows = ratings(row("a", 1600), row("b", 1400))  # decayed mean is exactly 1500: no shift
    assert show(rows)["a"]["elo"] == round(dec(1600, 31))


def test_anchor_puts_the_mean_of_active_established_fighters_at_1500():
    rows = ratings(row("a", 1700), row("b", 1500),
                   row("rookie", 1900, n=2, ufc=2),          # n < 3: not in the mean
                   row("gone", 1900, last="2024-01-01"))     # no bout in 730 days: not in the mean
    shift = 1500 - (dec(1700, 31) + 1500) / 2
    out = show(rows)
    assert out["a"]["elo"] == round(dec(1700, 31) + shift)
    assert out["b"]["elo"] == round(1500 + shift)
    assert out["rookie"]["elo"] == round(dec(1900, 31) + shift)
    assert out["gone"]["elo"] == round(dec(1900, (NOW - date(2024, 1, 1)).days) + shift)


def test_no_established_active_fighter_means_no_shift():
    assert show(ratings(row("a", 1600, n=1)))["a"]["elo"] == round(dec(1600, 31))


def test_peak_is_shown_on_the_anchored_scale_undecayed():
    out = show(ratings(row("a", 1700, peak=1800), row("b", 1300, peak=None)))
    assert out["a"]["elo_peak"] == 1800  # decayed mean is 1500: no shift
    assert out["b"]["elo_peak"] is None


def test_tags_provisional_under_three_ufc_bouts_and_inactive_after_a_year():
    out = show(ratings(row("new", 1500, n=8, ufc=2), row("vet", 1500, ufc=3),
                       row("idle", 1500, last="2025-10-31"), row("active", 1500, last="2025-11-01")))
    assert (out["new"]["elo_provisional"], out["vet"]["elo_provisional"]) == (True, False)
    assert (out["idle"]["elo_inactive"], out["active"]["elo_inactive"]) == (True, False)


def test_p4p_ranks_top_15_men_and_top_10_women_among_eligible():
    men = [row(f"m{i:02}", 1800 - 10 * i) for i in range(20)]
    women = [row(f"w{i:02}", 1700 - 10 * i, gender="F") for i in range(12)]
    ineligible = [row("few", 2000, ufc=4), row("idle", 2000, last="2025-10-01"), row("nog", 2000, gender=None)]
    out = show(ratings(*men, *women, *ineligible))
    assert [out[f"m{i:02}"]["p4p_rank"] for i in range(16)] == [*range(1, 16), None]
    assert [out[f"w{i:02}"]["p4p_rank"] for i in range(11)] == [*range(1, 11), None]
    assert out["few"]["p4p_rank"] is out["idle"]["p4p_rank"] is out["nog"]["p4p_rank"] is None


def test_ranking_covers_everyone_rated_not_just_the_requested_fighters():
    assert show(ratings(row("champ", 1900), row("a", 1700), row("b", 1500)), ["a"])["a"]["p4p_rank"] == 2


def test_best_win_is_shown_on_the_display_scale():
    rows = ratings(row("a", 1700, best=("z", "Zed", 1650.4, "2025-03-01")), row("b", 1500))
    shift = 1500 - (dec(1700, 31) + 1500) / 2
    out = show(rows)
    assert out["a"]["best_win"] == {"opponent_id": "z", "opponent": "Zed", "elo": round(1650.4 + shift),
                                    "date": "2025-03-01"}
    assert out["b"]["best_win"] is None


def test_best_win_without_a_name_falls_back_to_the_id():
    out = show(ratings(row("a", 1500, best=("z", None, 1500.0, "2025-03-01"))))
    assert out["a"]["best_win"]["opponent"] == "z"
