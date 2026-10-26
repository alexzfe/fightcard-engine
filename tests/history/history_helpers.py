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


# --- frozen rating parity (tests/fixtures/history/rating_parity.json) ------------------------------------------

PARITY = FIXTURES / "rating_parity.json"


def rating_cone(bouts: list[dict], fighter_ids, as_of: str) -> list[dict]:
    """The bouts `fighter_ids`' rows depend on, in input order: each fighter's own bouts up to `as_of`, then each
    opponent's bouts before that bout's day (their pre-day rating, K and decay clock), and so on back in time."""
    need = dict.fromkeys(fighter_ids, as_of)
    keep = []
    for b in reversed(bouts):
        hit = False
        for f, o in ((b["fighter_a"], b["fighter_b"]), (b["fighter_b"], b["fighter_a"])):
            if need.get(f, "") >= b["date"]:
                hit = True
                if need.get(o, "") < (before := _day_before(b["date"])):
                    need[o] = before
        if hit:
            keep.append(b)
    return keep[::-1]


def _day_before(day: str) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def _json_row(row: dict) -> dict:
    import pandas as pd
    return {k: (None if not isinstance(v, (list, dict)) and pd.isna(v) else
                int(v) if k in ("n_bouts", "n_ufc_bouts") else float(v) if isinstance(v, float) else v)
            for k, v in row.items()}


def parity_fixture(conn, model_dir: Path, fighters: dict[str, list[str]]) -> dict:
    """Research's export rows (`model_dir/fighter_rating.parquet`) for `fighters` ({id: [why picked]}), with the
    store's bouts their rows depend on (research's own bouts, up to the export's as_of) and the names their best
    wins need. `rate` over these bouts must reproduce the rows: the parity of `-m seed`, frozen."""
    import json

    import pandas as pd

    from fightcard.history.store import names, rating_bouts
    as_of = json.loads((model_dir / "manifest.json").read_text())["as_of"]
    research = {r["fight_id"] for r in read_csv(SEED_CSV / "fight_data.csv")}
    bouts = rating_cone([b for b in rating_bouts(conn, as_of) if b["fight_id"] in research], fighters, as_of)
    want = pd.read_parquet(model_dir / "fighter_rating.parquet").set_index("fighter_id").loc[sorted(fighters)]
    expected = [_json_row({"fighter_id": fid, **row}) for fid, row in want.to_dict("index").items()]
    all_names = names(conn)
    best = sorted({r["best_win_opponent_id"] for r in expected} - {None})
    return {"as_of": as_of, "fighters": {f: fighters[f] for f in sorted(fighters)},
            "names": {f: all_names[f] for f in best}, "bouts": bouts, "expected": expected}


def write_parity(fixture: dict, path: Path = PARITY) -> None:
    """One bout / row per line, so a regenerated fixture diffs line by line."""
    import json
    dumps = lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False)  # noqa: E731
    lines = ["{", f' "as_of": {dumps(fixture["as_of"])},', f' "fighters": {dumps(fixture["fighters"])},',
             f' "names": {dumps(fixture["names"])},', ' "bouts": [']
    lines += [f"  {dumps(b)}," for b in fixture["bouts"]]
    lines[-1] = lines[-1].rstrip(",")
    lines += [" ],", ' "expected": [']
    lines += [f"  {dumps(r)}," for r in fixture["expected"]]
    lines[-1] = lines[-1].rstrip(",")
    lines += [" ]", "}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    # uv run python tests/history/history_helpers.py: re-ingest the seed cache into a temporary store and rewrite
    # rating_parity.json for the fighters it already lists (needs the seed data and model/fighter_rating.parquet)
    import json
    import tempfile

    from fightcard.history.ingest import ingest
    from fightcard.history.store import connect
    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(Path(tmp) / "history.sqlite")
        ingest(conn, SEED_HTML)
        fighters = json.loads(PARITY.read_text(encoding="utf-8"))["fighters"]
        write_parity(parity_fixture(conn, FIXTURES.parents[2] / "model", fighters))
        conn.close()
