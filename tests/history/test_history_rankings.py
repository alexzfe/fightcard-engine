"""Rankings modules (`fightcard.history.rankings`): P4P lists, the active-fighter index, division ranks and the
all-time peak list, on the displayed scale (`fightcard.history.display`), plus the size report that decides what the
site can carry."""

from __future__ import annotations

import gzip
import json
from datetime import date

import pandas as pd

from fightcard.history.display import fighter_ratings
from fightcard.history.rankings import (active_index, all_time_peaks, division_ranks, divisions, p4p, published,
                                        size_report)
from fightcard.history.stage import run_rankings
from fightcard.models import Rankings

NOW = date(2026, 11, 1)
RECENT = "2026-10-01"
OLD = "2024-01-01"  # more than 365 days before NOW: inactive


def ratings(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows).set_index("fighter_id")


def row(fid: str, rating: float, last: str = RECENT, n: int = 10, ufc: int = 10, peak: float | None = None,
        gender: str | None = "M") -> dict:
    return {"fighter_id": fid, "rating": rating, "last_bout_date": last, "n_bouts": n, "n_ufc_bouts": ufc,
            "peak": peak, "gender": gender, "best_win_opponent_id": None, "best_win_opponent": None,
            "best_win_rating": None, "best_win_date": None}


def bout(day: str, a: str, b: str, wc: str | None, fid: str | None = None) -> dict:
    return {"date": day, "fight_id": fid or f"{day}-{a}-{b}", "fighter_a": a, "fighter_b": b, "result": "win",
            "method": "KO/TKO", "ufc": True, "weight_class": wc}


NAMES = {f"m{i}": f"Man {i}" for i in range(20)} | {f"w{i}": f"Woman {i}" for i in range(12)}


# --- divisions ---------------------------------------------------------------------------------------------------

def test_division_is_the_weight_class_of_the_latest_bout():
    got = divisions([bout("2024-01-01", "a", "b", "Welterweight"), bout("2025-01-01", "a", "c", "Middleweight")])
    assert got["a"] == "Middleweight"
    assert got["b"] == "Welterweight"


def test_catchweight_open_weight_and_missing_classes_fall_back_to_the_last_real_division():
    got = divisions([bout("2024-01-01", "a", "b", "Lightweight"), bout("2025-01-01", "a", "c", "Catch Weight"),
                     bout("2025-06-01", "a", "d", "Open Weight"), bout("2025-09-01", "a", "e", None)])
    assert got["a"] == "Lightweight"


def test_a_fighter_with_only_catchweight_bouts_has_no_division():
    assert "a" not in divisions([bout("2025-01-01", "a", "b", "Catch Weight")])


def test_input_order_does_not_matter():
    bouts = [bout("2025-01-01", "a", "c", "Middleweight"), bout("2024-01-01", "a", "b", "Welterweight")]
    assert divisions(bouts)["a"] == "Middleweight"


# --- p4p ---------------------------------------------------------------------------------------------------------

def pool() -> pd.DataFrame:
    men = [row(f"m{i}", 1800 - 10 * i) for i in range(20)]
    women = [row(f"w{i}", 1700 - 10 * i, gender="F") for i in range(12)]
    return ratings(*men, *women)


def test_p4p_lists_the_top_15_men_and_top_10_women_in_order():
    out = p4p(pool(), NAMES, {}, NOW)
    assert [r["id"] for r in out["men"]] == [f"m{i}" for i in range(15)]
    assert [r["id"] for r in out["women"]] == [f"w{i}" for i in range(10)]
    assert [r["rank"] for r in out["men"]] == list(range(1, 16))


def test_p4p_matches_the_fighter_pages_rank_and_elo():
    rows = pool()
    pages = fighter_ratings(list(rows.index), rows, NOW)
    for entry in p4p(rows, NAMES, {}, NOW)["men"]:
        assert (entry["rank"], entry["elo"]) == (pages[entry["id"]]["p4p_rank"], pages[entry["id"]]["elo"])


def test_p4p_skips_inactive_fighters_and_those_under_five_ufc_bouts():
    rows = ratings(row("idle", 2000, last=OLD), row("new", 1990, ufc=4), row("m1", 1500))
    assert [r["id"] for r in p4p(rows, NAMES, {}, NOW)["men"]] == ["m1"]


def test_p4p_entries_carry_name_and_division():
    entry = p4p(ratings(row("m1", 1600)), NAMES, {"m1": "Lightweight"}, NOW)["men"][0]
    assert entry == {"rank": 1, "id": "m1", "name": "Man 1", "elo": entry["elo"], "division": "Lightweight"}


def test_an_unnamed_fighter_shows_their_id():
    assert p4p(ratings(row("x9", 1600)), {}, {}, NOW)["men"][0]["name"] == "x9"


# --- active index ------------------------------------------------------------------------------------------------

def test_active_index_holds_every_active_fighter_with_a_ufc_bout_best_first():
    rows = ratings(row("m1", 1500), row("m2", 1600, ufc=1), row("idle", 1900, last=OLD),
                   row("regional", 1700, ufc=0))
    assert [r["id"] for r in active_index(rows, NAMES, {}, NOW)] == ["m2", "m1"]


def test_active_index_rows_flag_provisional_and_carry_p4p_rank():
    rows = ratings(row("m1", 1600), row("m2", 1500, ufc=2))
    got = {r["id"]: r for r in active_index(rows, NAMES, {"m1": "Featherweight"}, NOW)}
    assert got["m1"] == {"id": "m1", "name": "Man 1", "elo": got["m1"]["elo"], "division": "Featherweight",
                         "p4p_rank": 1, "provisional": False}
    assert (got["m2"]["provisional"], got["m2"]["p4p_rank"], got["m2"]["division"]) == (True, None, None)


def test_active_index_elo_matches_the_fighter_pages():
    rows = pool()
    pages = fighter_ratings(list(rows.index), rows, NOW)
    assert all(r["elo"] == pages[r["id"]]["elo"] for r in active_index(rows, NAMES, {}, NOW))


# --- published (data/rankings.json) ------------------------------------------------------------------------------

def test_published_is_the_p4p_and_active_lists_as_of_the_day():
    rows = pool()
    got = published(rows, NAMES, {"m0": "Welterweight"}, NOW, set())
    assert set(got) == {"as_of", "p4p", "active"}
    assert got["as_of"] == "2026-11-01"
    assert {k: [{c: v for c, v in e.items() if c != "image"} for e in es] for k, es in got["p4p"].items()} == \
        p4p(rows, NAMES, {"m0": "Welterweight"}, NOW)
    assert got["active"] == active_index(rows, NAMES, {"m0": "Welterweight"}, NOW)


def test_published_p4p_entries_carry_an_image_only_for_fighters_with_a_headshot():
    got = published(pool(), NAMES, {}, NOW, {"m1", "w0", "m30"})
    images = {e["id"]: e["image"] for es in got["p4p"].values() for e in es}
    assert images["m1"] == "images/m1.webp" and images["w0"] == "images/w0.webp"
    assert images["m0"] is None and images["w3"] is None
    assert set(images.values()) == {None, "images/m1.webp", "images/w0.webp"}  # m30 isn't ranked: no entry


def test_published_active_rows_never_carry_an_image():
    got = published(pool(), NAMES, {}, NOW, {f"m{i}" for i in range(20)})
    assert all("image" not in r for r in got["active"])


def test_published_agrees_with_itself_by_construction():
    """Every P4P fighter is in `active` with the same rank, and no other active row has a rank (the `Rankings`
    consistency check publish enforces)."""
    got = published(pool(), NAMES, {}, NOW, set())
    Rankings.model_validate(got)
    ranks = {r["id"]: r["p4p_rank"] for r in got["active"] if r["p4p_rank"] is not None}
    assert ranks == {e["id"]: e["rank"] for es in got["p4p"].values() for e in es}


# --- division ranks ----------------------------------------------------------------------------------------------

def test_division_ranks_rank_each_division_separately_top_15():
    rows = ratings(*[row(f"m{i}", 1800 - 10 * i) for i in range(20)])
    divs = {f"m{i}": "Lightweight" if i < 17 else "Welterweight" for i in range(20)}
    out = division_ranks(rows, NAMES, divs, NOW)
    assert [r["id"] for r in out["Lightweight"]] == [f"m{i}" for i in range(15)]
    assert [(r["rank"], r["id"]) for r in out["Welterweight"]] == [(1, "m17"), (2, "m18"), (3, "m19")]


def test_division_ranks_use_the_p4p_eligibility_and_skip_fighters_without_a_division():
    rows = ratings(row("idle", 2000, last=OLD), row("new", 1990, ufc=4), row("m1", 1500), row("m2", 1400))
    divs = {"idle": "Lightweight", "new": "Lightweight", "m1": "Lightweight"}
    assert [r["id"] for r in division_ranks(rows, NAMES, divs, NOW)["Lightweight"]] == ["m1"]


# --- all-time peaks ----------------------------------------------------------------------------------------------

def test_all_time_peaks_include_retired_fighters_highest_first():
    rows = ratings(row("m1", 1500, peak=1700), row("legend", 1500, last="2005-01-01", peak=1900),
                   row("m2", 1500, peak=None))
    out = all_time_peaks(rows, NAMES, NOW)
    assert [(r["rank"], r["id"]) for r in out] == [(1, "legend"), (2, "m1")]


def test_all_time_peaks_are_on_the_fighter_pages_scale():
    rows = ratings(row("m1", 1700, peak=1750), row("m2", 1400, peak=1650))
    pages = fighter_ratings(list(rows.index), rows, NOW)
    assert {r["id"]: r["peak"] for r in all_time_peaks(rows, NAMES, NOW)} == {
        "m1": pages["m1"]["elo_peak"], "m2": pages["m2"]["elo_peak"]}


def test_all_time_peaks_can_be_cut_to_a_limit():
    rows = ratings(*[row(f"m{i}", 1500, peak=1900 - i) for i in range(10)])
    assert len(all_time_peaks(rows, NAMES, NOW, limit=3)) == 3


# --- size report -------------------------------------------------------------------------------------------------

def test_size_report_gives_rows_and_compact_json_bytes_raw_and_gzipped():
    data = [{"id": "a", "elo": 1500}, {"id": "b", "elo": 1400}]
    (line,) = size_report({"thing": data})
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    assert line == {"module": "thing", "rows": 2, "raw_bytes": len(raw),
                    "gzip_bytes": len(gzip.compress(raw, mtime=0))}


def test_size_report_counts_rows_across_a_dict_of_lists():
    (line,) = size_report({"p4p": {"men": [1, 2, 3], "women": [4]}})
    assert line["rows"] == 4


# --- CLI: fightcard history rankings ----------------------------------------------------------------------------

def test_rankings_command_needs_an_existing_store(tmp_path, capsys):
    assert run_rankings(tmp_path / "nope.sqlite", NOW, report=True) == 1
    assert "nope.sqlite" in capsys.readouterr().err


def test_rankings_report_prints_one_line_per_module(tmp_path, capsys):
    from fightcard.history.store import connect
    db = tmp_path / "history.sqlite"
    connect(db).close()
    assert run_rankings(db, NOW, report=True) == 0
    out = capsys.readouterr().out
    for module in ("p4p", "active_index", "division_ranks", "all_time_peaks"):
        assert module in out


def test_rankings_command_takes_its_ratings_from_the_shared_history_pass(tmp_path, monkeypatch):
    from fightcard.history import rating, stage
    from fightcard.history.store import connect
    calls = []
    monkeypatch.setattr(stage, "history_pass", lambda *a: calls.append(a) or rating.history_pass(*a), raising=False)
    db = tmp_path / "history.sqlite"
    connect(db).close()
    assert run_rankings(db, NOW, report=True) == 0
    assert [as_of for _, as_of in calls] == [NOW.isoformat()]


# --- the model and the rating params agree on the P4P sizes -------------------------------------------------------------

def test_the_rankings_model_limits_equal_the_p4p_sizes_in_the_rating_params():
    """models.py can't import history.rating (import cycle), so its max_length 15 / 10 are a copy of PARAMS' p4p_men /
    p4p_women. Raising one without the other makes publish refuse the whole generation."""
    from fightcard.history.rating import PARAMS
    from fightcard.models import P4P

    def limit(field: str) -> int:
        (found,) = [m.max_length for m in P4P.model_fields[field].metadata if hasattr(m, "max_length")]
        return found

    assert (limit("men"), limit("women")) == (PARAMS["display"]["p4p_men"], PARAMS["display"]["p4p_women"])


# --- `fightcard history rankings` rates up to the UTC date by default --------------------------------------------------

def test_the_default_as_of_is_the_utc_date_not_the_local_one(tmp_path, monkeypatch):
    """Like score and images: near midnight a dev machine's local date can be a day ahead of the one score writes."""
    from argparse import Namespace
    from datetime import UTC, datetime

    from fightcard.history import stage

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 31, 23, 30, tzinfo=UTC) if tz is UTC else datetime(2026, 11, 1, 9, 30)

    seen = []
    monkeypatch.setattr(stage, "datetime", Clock, raising=False)
    monkeypatch.setattr(stage, "run_rankings", lambda db, now, report: seen.append(now) or 0)
    assert stage.run(Namespace(action="rankings", db=tmp_path / "h.sqlite", as_of=None, report=False)) == 0
    assert seen == [date(2026, 10, 31)]
