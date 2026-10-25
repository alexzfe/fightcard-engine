"""Current Elo: the snapshot rating rolled forward over results since the export."""

from __future__ import annotations

import pandas as pd
import pytest

from fightcard.models import Fighter, PastFight, Record
from fightcard.score.elo import current_elo, update

AS_OF = "2026-10-14"


def snap(*rows: tuple) -> pd.DataFrame:
    """Rows of (fighter_id, elo, n_ufc_bouts, w, l, d, nc)."""
    cols = ["fighter_id", "elo", "n_ufc_bouts", "record_w", "record_l", "record_d", "record_nc"]
    return pd.DataFrame(rows, columns=cols).set_index("fighter_id")


def past(opponent_id: str | None, result: str, date: str) -> PastFight:
    return PastFight(result=result, opponent="X", opponent_id=opponent_id, event="UFC", date=date)


def fighter(fid: str, w=0, l=0, d=0, nc=0, fights: list[PastFight] | None = None) -> Fighter:
    return Fighter(id=fid, name=fid, record=Record(w=w, l=l, d=d, nc=nc), recent_fights=fights)


def test_update_matches_the_research_formula():
    assert update(1500, 1500, 1.0) == pytest.approx(16.0)
    assert update(1500, 1500, 0.5) == pytest.approx(0.0)
    assert update(1600, 1400, 0.5) == pytest.approx(-update(1400, 1600, 0.5))
    assert update(1700, 1500, 1.0) == pytest.approx(32 * (1 - 1 / (1 + 10 ** (-200 / 400))))


def test_no_new_fights_keeps_the_snapshot_rating_rounded():
    s = snap(("a", 1790.83, 20, 10, 0, 0, 0))
    assert current_elo({"a": fighter("a", w=10)}, s, AS_OF) == {"a": 1791}


def test_a_newer_win_counts_once_although_both_fighters_list_it():
    s = snap(("a", 1500.0, 5, 3, 2, 0, 0), ("b", 1500.0, 5, 3, 2, 0, 0))
    fighters = {
        "a": fighter("a", w=4, l=2, fights=[past("b", "win", "2026-10-24")]),
        "b": fighter("b", w=3, l=3, fights=[past("a", "loss", "2026-10-24")]),
    }
    assert current_elo(fighters, s, AS_OF) == {"a": 1516, "b": 1484}


def test_fights_on_or_before_as_of_are_already_in_the_snapshot():
    s = snap(("a", 1500.0, 5, 4, 0, 0, 0))
    fighters = {"a": fighter("a", w=4, fights=[past("z", "win", AS_OF), past("z", "win", "2026-09-01")])}
    assert current_elo(fighters, s, AS_OF) == {"a": 1500}


def test_opponent_outside_fighters_json_uses_their_snapshot_rating():
    s = snap(("a", 1500.0, 5, 3, 0, 0, 0), ("z", 1700.0, 9, 9, 0, 0, 0))
    fighters = {"a": fighter("a", w=4, fights=[past("z", "win", "2026-10-24")])}
    assert current_elo(fighters, s, AS_OF) == {"a": round(1500 + update(1500, 1700, 1.0))}


def test_nc_and_draw():
    s = snap(("a", 1600.0, 5, 3, 0, 0, 0), ("b", 1400.0, 5, 3, 0, 0, 0))
    nc = {"a": fighter("a", w=3, nc=1, fights=[past("b", "nc", "2026-10-24")])}
    assert current_elo(nc, s, AS_OF) == {"a": 1600}
    draw = {"a": fighter("a", w=3, d=1, fights=[past("b", "draw", "2026-10-24")])}
    assert current_elo(draw, s, AS_OF) == {"a": round(1600 + update(1600, 1400, 0.5))}


def test_same_day_bouts_see_the_ratings_from_before_the_day():
    # a beats b and c on one night (an old tournament): both use a's pre-day 1500.
    s = snap(*[(f, 1500.0, 5, 3, 0, 0, 0) for f in "abc"])
    fighters = {"a": fighter("a", w=5, fights=[past("b", "win", "2026-10-24"), past("c", "win", "2026-10-24")])}
    assert current_elo(fighters, s, AS_OF) == {"a": 1532}


def test_days_apply_in_order():
    s = snap(("a", 1500.0, 5, 3, 0, 0, 0), ("b", 1500.0, 5, 3, 0, 0, 0))
    fighters = {"a": fighter("a", w=5, fights=[past("b", "win", "2026-11-07"), past("b", "win", "2026-10-24")])}
    first = 1500 + update(1500, 1500, 1.0)
    second = first + update(first, 1500 - update(1500, 1500, 1.0), 1.0)
    assert current_elo(fighters, s, AS_OF) == {"a": round(second)}


def test_missing_newer_fights_gives_none():
    # record says two bouts since the export; only one is known
    s = snap(("a", 1500.0, 5, 3, 0, 0, 0))
    fighters = {"a": fighter("a", w=5, fights=[past("b", "win", "2026-10-24")])}
    assert current_elo(fighters, s, AS_OF) == {"a": None}


def test_newer_fight_without_opponent_id_gives_none():
    s = snap(("a", 1500.0, 5, 3, 0, 0, 0))
    fighters = {"a": fighter("a", w=4, fights=[past(None, "win", "2026-10-24")])}
    assert current_elo(fighters, s, AS_OF) == {"a": None}


def test_no_ufc_history_gives_none():
    s = snap(("rookie", 1487.0, 0, 5, 0, 0, 0))
    fighters = {"rookie": fighter("rookie", w=5), "nobody": fighter("nobody", w=3)}
    assert current_elo(fighters, s, AS_OF) == {"rookie": None, "nobody": None}


def test_debut_since_the_export_starts_at_1500():
    s = snap(("b", 1500.0, 5, 3, 0, 0, 0))
    fighters = {"new": fighter("new", w=1, fights=[past("b", "win", "2026-10-24")])}
    assert current_elo(fighters, s, AS_OF) == {"new": 1516}
