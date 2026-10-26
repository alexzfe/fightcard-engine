"""History store tests. `FIXTURES` is a committed subset of the research repo's ufcstats cache (7 event pages, 2
fighter pages, the completed listing trimmed to those events) with the matching rows of research's CSVs.

`seed` tests run against the full cache: `uv run pytest -m seed`. They skip when it isn't there. Point
`FIGHTCARD_SEED_DIR` at a fight-auto-research `data/` folder to use another copy."""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "history"
SEED_DIR = Path(os.environ.get("FIGHTCARD_SEED_DIR", Path.home() / "Projects" / "fight-auto-research" / "data"))
SEED_HTML = SEED_DIR / "raw_html" / "ufcstats"
SEED_CSV = SEED_DIR / "raw" / "ufcstats"

# research fight_data.csv (result, result_details) -> the event page's method column
_CODE = {"KO/TKO": "KO/TKO", "TKO - Doctor's Stoppage": "KO/TKO", "Submission": "SUB", "DQ": "DQ",
         "Could Not Continue": "CNC", "Overturned": "Overturned", "Other": "Other"}
_DECISION = {"Unanimous": "U-DEC", "Split": "S-DEC", "Majority": "M-DEC", "Decision": "Decision"}


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def research_method(row: dict) -> str | None:
    if row["result"] == "Decision":
        return _DECISION.get(row["result_details"])
    return _CODE.get(row["result"])


def cross_check(conn, fight_rows: list[dict]) -> dict:
    """Compare the store's fights with research's fight_data.csv rows: every fight_id present, on the same event,
    same two fighters, same winner (an id, "Draw" or "NC") and the same method. Returns the mismatches by kind."""
    ours = {r[0]: r[1:] for r in conn.execute(
        "SELECT fight_id, event_id, red, blue, outcome, winner, method FROM fights")}
    bad: dict[str, list[str]] = {"missing": [], "event": [], "fighters": [], "winner": [], "method": []}
    for r in fight_rows:
        got = ours.get(r["fight_id"])
        if got is None:
            bad["missing"].append(r["fight_id"])
            continue
        event_id, red, blue, outcome, winner, method = got
        if event_id != r["event_id"]:
            bad["event"].append(r["fight_id"])
        if {red, blue} != {r["fighter_1"], r["fighter_2"]}:
            bad["fighters"].append(r["fight_id"])
        want = {"Draw": ("draw", None), "NC": ("nc", None)}.get(r["winner"], ("win", r["winner"]))
        if (outcome, winner) != want:
            bad["winner"].append(r["fight_id"])
        if method != research_method(r):
            bad["method"].append(r["fight_id"])
    return {k: v for k, v in bad.items() if v}


def needs_seed() -> None:
    for p in (SEED_HTML / "event-details", SEED_HTML / "listings" / "events_completed.html",
              SEED_CSV / "fight_data.csv", SEED_CSV / "event_source.csv"):
        if not p.exists():
            pytest.skip(f"seed data not present: {p}")
