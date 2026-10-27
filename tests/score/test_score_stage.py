"""`fightcard --data data score`: tiers, no-history and staleness guards, freezing, index.model."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from fightcard.cli import main
from fightcard.history.display import fighter_ratings
from fightcard.history.rating import history_ratings
from fightcard.history.store import connect, upsert_event
from fightcard.models import Event, Fight, Fighter, Index, Record, Result
from fightcard.store import Store

NOW = "2026-11-01T06:00:00Z"


def set_thresholds(model_dir: Path, low_max: float, medium_max: float) -> None:
    p = model_dir / "thresholds.json"
    t = json.loads(p.read_text())
    t.update(low_max=low_max, medium_max=medium_max)
    p.write_text(json.dumps(t))


def fighter_from(row: dict, **record) -> Fighter:
    rec = {"w": row["record_w"], "l": row["record_l"], "d": row["record_d"], "nc": row["record_nc"], **record}
    return Fighter(id=row["fighter_id"], name=row["name"], record=Record(**rec))


def event(eid: str, date: str, fights: list[Fight], status="upcoming") -> Event:
    return Event(id=eid, slug=eid, name=f"UFC {eid}", date=date, status=status,
                 location="Las Vegas, Nevada, USA", fights=fights)


@pytest.fixture
def data(tmp_path, veterans, real_snapshot) -> tuple[Path, Store]:
    v = veterans
    rookie = real_snapshot[real_snapshot["n_ufc_bouts"] == 0].iloc[0].to_dict()
    root = tmp_path / "data"
    store = Store(root)
    fighters = {r["fighter_id"]: fighter_from(r) for r in [*v, rookie]}
    fighters["nobody"] = Fighter(id="nobody", name="Not In Snapshot", record=Record(w=3))
    store.save_fighters(fighters)
    store.save_event(event("future", "2026-11-14", [
        Fight(id="ok", red=v[0]["fighter_id"], blue=v[1]["fighter_id"], weight_class="Lightweight"),
        Fight(id="rookie", red=v[2]["fighter_id"], blue=rookie["fighter_id"], weight_class="Lightweight"),
        Fight(id="unknown", red=v[3]["fighter_id"], blue="nobody", weight_class="Lightweight"),
    ]))
    store.save_index(Index(generated_at=NOW, upcoming=["future"], recent=[]))
    return root, store


def score(root: Path, model_dir: Path, now: str = NOW) -> int:
    """Tiers only (`--no-history`): the tier tests don't need a history store."""
    return main(["--data", str(root), "score", "--model-dir", str(model_dir), "--no-history", "--now", now])


def tiers(store: Store, eid: str) -> dict:
    return {f.id: f.finish_tier for f in store.load_event(eid).fights}


def test_scores_upcoming_fights_and_applies_no_history_guard(data, model_dir):
    root, store = data
    set_thresholds(model_dir, -1.0, 2.0)  # every scored bout is "medium"
    assert score(root, model_dir) == 0
    ev = store.load_event("future")
    assert tiers(store, "future") == {"ok": "medium", "rookie": None, "unknown": None}
    assert all(f.tier_frozen_at is None for f in ev.fights)  # event is in the future
    assert not (root / "stale_fighters.json").exists()


def test_tier_follows_thresholds(data, model_dir):
    root, store = data
    set_thresholds(model_dir, 2.0, 3.0)
    score(root, model_dir)
    assert tiers(store, "future")["ok"] == "low"
    set_thresholds(model_dir, -2.0, -1.0)
    score(root, model_dir)
    assert tiers(store, "future")["ok"] == "high"


def test_stale_record_nulls_tier_and_is_listed(data, model_dir, veterans):
    root, store = data
    set_thresholds(model_dir, -1.0, 2.0)
    fighters = store.load_fighters()
    v1 = veterans[1]
    fighters[v1["fighter_id"]] = fighter_from(v1, w=v1["record_w"] + 1)
    store.save_fighters(fighters)
    score(root, model_dir)
    assert tiers(store, "future")["ok"] is None
    stale = json.loads((root / "stale_fighters.json").read_text())
    assert stale == [{"event_id": "future", "fighter_id": v1["fighter_id"], "name": v1["name"]}]
    # once the record matches again the file goes away and the bout is scored
    fighters[v1["fighter_id"]] = fighter_from(v1)
    store.save_fighters(fighters)
    score(root, model_dir)
    assert tiers(store, "future")["ok"] == "medium"
    assert not (root / "stale_fighters.json").exists()


def test_index_model_counts_the_stale_fighters(data, model_dir, veterans):
    """index.model.stale_fighters: how many fighters the guard nulled (each once, however many bouts or events)."""
    root, store = data
    score(root, model_dir)
    assert store.load_index().model.stale_fighters == 0
    fighters = store.load_fighters()
    for v in veterans[:2]:
        fighters[v["fighter_id"]] = fighter_from(v, w=v["record_w"] + 1)
    store.save_fighters(fighters)
    store.save_event(event("later", "2026-11-21", [
        Fight(id="again", red=veterans[0]["fighter_id"], blue=veterans[2]["fighter_id"], weight_class="Lightweight")]))
    score(root, model_dir)
    assert len(json.loads((root / "stale_fighters.json").read_text())) == 3  # entries: per fighter per event
    assert store.load_index().model.stale_fighters == 2


def test_past_event_tiers_are_frozen_once(data, model_dir, veterans):
    root, store = data
    v = veterans
    store.save_event(event("today", "2026-11-01", [
        Fight(id="t1", red=v[4]["fighter_id"], blue=v[5]["fighter_id"], weight_class="Welterweight")]))
    set_thresholds(model_dir, -1.0, 2.0)
    score(root, model_dir)
    f = store.load_event("today").fights[0]
    assert (f.finish_tier, f.tier_frozen_at) == ("medium", NOW)
    set_thresholds(model_dir, 2.0, 3.0)  # would now be "low"
    score(root, model_dir, now="2026-11-02T06:00:00Z")
    f = store.load_event("today").fights[0]
    assert (f.finish_tier, f.tier_frozen_at) == ("medium", NOW)
    assert tiers(store, "future")["ok"] == "low"  # unfrozen events still follow the model


def test_frozen_null_tier_stays_null(data, model_dir, veterans):
    root, store = data
    v = veterans
    store.save_event(event("old", "2026-10-01", [
        Fight(id="o1", red=v[4]["fighter_id"], blue=v[5]["fighter_id"], finish_tier=None,
              tier_frozen_at="2026-10-01T06:00:00Z")], status="completed"))
    score(root, model_dir)
    f = store.load_event("old").fights[0]
    assert (f.finish_tier, f.tier_frozen_at) == (None, "2026-10-01T06:00:00Z")


def test_freezing_after_results_keeps_the_pre_fight_tier(data, model_dir, veterans):
    """Results already scraped means records moved because of this very bout: keep the
    tier computed before it, freeze it, and don't report the fighters as stale."""
    root, store = data
    v = veterans
    store.save_event(event("done", "2026-10-31", [
        Fight(id="d1", red=v[4]["fighter_id"], blue=v[5]["fighter_id"], finish_tier="high",
              result=Result(winner=v[4]["fighter_id"], method="KO/TKO", round=1))], status="completed"))
    fighters = store.load_fighters()
    fighters[v[4]["fighter_id"]] = fighter_from(v[4], w=v[4]["record_w"] + 1)
    fighters[v[5]["fighter_id"]] = fighter_from(v[5], l=v[5]["record_l"] + 1)
    store.save_fighters(fighters)
    set_thresholds(model_dir, 2.0, 3.0)
    score(root, model_dir)
    f = store.load_event("done").fights[0]
    assert (f.finish_tier, f.tier_frozen_at) == ("high", NOW)
    assert not (root / "stale_fighters.json").exists()


def test_index_model_from_manifest(data, model_dir):
    root, store = data
    score(root, model_dir)
    m = json.loads((model_dir / "manifest.json").read_text())
    info = store.load_index().model
    assert (info.as_of, info.version) == (m["as_of"], m["version"])
    assert info.trees == 300 and len(info.importance) == 7  # the how-it-works summary (test_score_summary.py)


def test_missing_model_dir_fails(data, tmp_path, capsys):
    root, _ = data
    assert score(root, tmp_path / "nope") == 1
    assert "model" in capsys.readouterr().err


def test_score_says_which_model_it_scores_with(data, model_dir, capsys):
    root, _ = data
    score(root, model_dir)
    manifest = json.loads((model_dir / "manifest.json").read_text())
    assert f"score: model {manifest['version']} (as_of {manifest['as_of']}) from {model_dir}" in capsys.readouterr().err


def test_a_model_dir_that_does_not_load_gives_way_to_the_fallback_and_says_so(data, model_dir, tmp_path, capsys):
    root, store = data
    broken = tmp_path / "current"
    shutil.copytree(model_dir, broken)
    (broken / "model.txt").write_text("not a booster")
    assert main(["--data", str(root), "score", "--model-dir", str(broken), "--fallback-model-dir", str(model_dir),
                 "--no-history", "--now", NOW]) == 0
    err = capsys.readouterr().err
    assert "ERROR" in err and str(broken) in err and f"from {model_dir}" in err, err
    assert tiers(store, "future")["ok"] in ("low", "medium", "high")
    assert store.load_index().model.version == json.loads((model_dir / "manifest.json").read_text())["version"]


def test_a_model_dir_that_loads_ignores_the_fallback(data, model_dir, tmp_path, capsys):
    root, _ = data
    main(["--data", str(root), "score", "--model-dir", str(model_dir), "--fallback-model-dir", str(tmp_path / "nope"),
          "--no-history", "--now", NOW])
    assert "ERROR" not in capsys.readouterr().err


def test_a_missing_model_dir_and_a_missing_fallback_still_fail(data, tmp_path, capsys):
    root, _ = data
    assert main(["--data", str(root), "score", "--model-dir", str(tmp_path / "a"),
                 "--fallback-model-dir", str(tmp_path / "b"), "--no-history", "--now", NOW]) == 1
    assert "model" in capsys.readouterr().err


def test_default_model_dir_and_now(data, monkeypatch):
    """Without flags: model/ relative to the cwd, and the current UTC time."""
    root, store = data
    monkeypatch.chdir(Path(__file__).resolve().parents[2])
    assert main(["--data", str(root), "score", "--no-history"]) == 0
    assert tiers(store, "future")["ok"] in ("low", "medium", "high")


def test_unchanged_run_rewrites_identical_files(data, model_dir):
    root, store = data
    score(root, model_dir)
    before = (root / "events" / "future.json").read_text()
    score(root, model_dir, now="2026-11-02T06:00:00Z")
    assert (root / "events" / "future.json").read_text() == before


def history(tmp_path: Path, veterans: list[dict], extra: list[tuple[str, str, str, str]] = ()) -> Path:
    """A history store where v0 beats v1, v2 beats v3 and v4 beats v5 at UFC events on 2026-09-01 and 2026-10-01,
    plus `extra` bouts (date, fight_id, winner, loser), each on its own UFC event."""
    db = tmp_path / "history" / "history.sqlite"
    conn = connect(db)
    ids = [v["fighter_id"] for v in veterans]
    bouts = [(day, f"{day}-{i}", ids[i], ids[i + 1]) for day in ("2026-09-01", "2026-10-01") for i in (0, 2, 4)]
    with conn:
        for day, fid, winner, loser in [*bouts, *extra]:
            fight = Fight(id=fid, red=winner, blue=loser, weight_class="Lightweight",
                          result=Result(winner=winner, outcome="win", method="U-DEC", round=3, time="5:00"))
            upsert_event(conn, Event(id=f"e-{fid}", slug="x", name=f"UFC {fid}", date=day, status="completed",
                                     fights=[fight]), is_ufc=True, source="listing")
        for v in veterans:
            conn.execute("INSERT OR REPLACE INTO fighters (fighter_id, name) VALUES (?, ?)", (v["fighter_id"], v["name"]))
    conn.close()
    return db


def expected_fields(db: Path, ids, now: str) -> dict:
    conn = connect(db)
    try:
        return fighter_ratings(ids, history_ratings(conn, as_of=now[:10]), date.fromisoformat(now[:10]))
    finally:
        conn.close()


def fields(f: Fighter) -> dict:
    return {"elo": f.elo, "elo_peak": f.elo_peak, "p4p_rank": f.p4p_rank, "elo_provisional": f.elo_provisional,
            "elo_inactive": f.elo_inactive, "best_win": None if f.best_win is None else f.best_win.model_dump()}


def score_with(root: Path, model_dir: Path, db: Path, now: str = NOW) -> int:
    return main(["--data", str(root), "score", "--model-dir", str(model_dir), "--history-db", str(db), "--now", now])


def test_writes_rating_fields_from_the_history_store(data, model_dir, veterans, tmp_path, capsys):
    root, store = data
    db = history(tmp_path, veterans)
    assert score_with(root, model_dir, db) == 0
    fighters = store.load_fighters()
    want = expected_fields(db, list(fighters), NOW)
    assert {fid: fields(f) for fid, f in fighters.items()} == want
    assert fighters[veterans[0]["fighter_id"]].elo > fighters[veterans[1]["fighter_id"]].elo
    assert fighters[veterans[0]["fighter_id"]].best_win.opponent == veterans[1]["name"]
    for fid in ("nobody", fighters_without_snapshot_history(fighters, veterans)):  # no bout in the store
        assert fields(fighters[fid]) == {"elo": None, "elo_peak": None, "p4p_rank": None, "elo_provisional": False,
                                         "elo_inactive": False, "best_win": None}
    assert "6 fighters with an Elo" in capsys.readouterr().err
    before = store.fighters_path.read_text()
    score_with(root, model_dir, db)
    assert store.fighters_path.read_text() == before


def fighters_without_snapshot_history(fighters: dict, veterans: list[dict]) -> str:
    """The rookie's id: the one fighter who is neither a veteran nor "nobody"."""
    [rookie] = set(fighters) - {v["fighter_id"] for v in veterans} - {"nobody"}
    return rookie


def test_a_fighter_with_many_bouts_since_the_model_export_is_rated(data, model_dir, veterans, tmp_path):
    """No roll-forward limit: four wins after the export's as_of (2026-10-14) are all in the rating."""
    root, store = data
    v0, v1 = veterans[0]["fighter_id"], veterans[1]["fighter_id"]
    later = [(f"2026-10-{d}", f"late{d}", v0, v1) for d in (17, 20, 24, 28)]
    db = history(tmp_path, veterans, later)
    fighters = store.load_fighters()
    fighters[v0] = fighters[v0].model_copy(update={"record": Record(w=fighters[v0].record.w + 6)})
    store.save_fighters(fighters)
    assert score_with(root, model_dir, db) == 0
    f = store.load_fighters()[v0]
    assert f.elo is not None
    assert fields(f) == expected_fields(db, [v0], NOW)[v0]
    before_export = expected_fields(history(tmp_path / "b", veterans), [v0], NOW)[v0]
    assert f.elo > before_export["elo"]


def test_decay_and_the_bouts_counted_follow_now(data, model_dir, veterans, tmp_path):
    root, store = data
    db = history(tmp_path, veterans, [("2027-06-01", "future", veterans[1]["fighter_id"], veterans[0]["fighter_id"])])
    score_with(root, model_dir, db, now="2026-11-01T06:00:00Z")
    early = store.load_fighters()[veterans[0]["fighter_id"]]
    score_with(root, model_dir, db, now="2027-11-01T06:00:00Z")
    late = store.load_fighters()[veterans[0]["fighter_id"]]
    assert fields(early) == expected_fields(db, [veterans[0]["fighter_id"]], "2026-11-01")[veterans[0]["fighter_id"]]
    assert fields(late) == expected_fields(db, [veterans[0]["fighter_id"]], "2027-11-01")[veterans[0]["fighter_id"]]
    assert early.elo != late.elo


def snapshot_files(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*.json"))}


def unrelated(n: int = 6) -> list[dict]:
    """Stand-ins for `veterans` in `history`: fighters who aren't in data/."""
    return [{"fighter_id": f"x{i}", "name": f"X {i}"} for i in range(n)]


def test_a_missing_history_store_fails_and_writes_nothing(data, model_dir, tmp_path, capsys):
    """No store (wrong path, volume not restored) must not publish every fighter as unrated."""
    root, store = data
    before = snapshot_files(root)
    db = tmp_path / "nope" / "history.sqlite"
    assert score_with(root, model_dir, db) == 1
    err = capsys.readouterr().err
    assert f"no history store at {db}" in err and "--no-history" in err
    assert snapshot_files(root) == before
    assert not db.exists()


@pytest.mark.parametrize("content", [b"", b"not a database at all, just some bytes" * 50], ids=["empty", "garbage"])
def test_an_unreadable_history_store_fails_with_one_line(data, model_dir, tmp_path, capsys, content):
    root, store = data
    before = snapshot_files(root)
    db = tmp_path / "history.sqlite"
    db.write_bytes(content)
    assert score_with(root, model_dir, db) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert [line for line in err.splitlines() if str(db) in line] == [
        f"score: history store {db} is unusable (not an ingested history store); run `fightcard history ingest`"]
    assert snapshot_files(root) == before


def test_a_never_ingested_history_store_fails(data, model_dir, tmp_path, capsys):
    """Schema but no events: `connect` ran, `ingest` never did."""
    root, store = data
    db = tmp_path / "history.sqlite"
    connect(db).close()
    assert score_with(root, model_dir, db) == 1
    assert "is unusable" in capsys.readouterr().err


def test_no_history_scores_tiers_and_leaves_rating_fields_as_they_are(data, model_dir, veterans, tmp_path):
    root, store = data
    set_thresholds(model_dir, -1.0, 2.0)
    v0 = veterans[0]["fighter_id"]
    fighters = store.load_fighters()
    fighters[v0] = fighters[v0].model_copy(update={"elo": 1612, "p4p_rank": 3, "elo_peak": 1650})
    store.save_fighters(fighters)
    before = store.fighters_path.read_text()
    assert main(["--data", str(root), "score", "--model-dir", str(model_dir), "--no-history", "--now", NOW]) == 0
    assert store.fighters_path.read_text() == before
    assert tiers(store, "future") == {"ok": "medium", "rookie": None, "unknown": None}


def test_no_history_and_history_db_are_exclusive(data, model_dir, tmp_path):
    root, _ = data
    with pytest.raises(SystemExit) as e:
        main(["--data", str(root), "score", "--model-dir", str(model_dir), "--no-history",
              "--history-db", str(tmp_path / "h.sqlite")])
    assert e.value.code == 2


def test_default_history_store_is_beside_the_data_dir_not_in_the_cwd(data, model_dir, veterans, tmp_path,
                                                                     monkeypatch):
    """Default `--history-db`: <--data>/../history/history.sqlite, whatever the cwd."""
    root, store = data
    assert history(root.parent, veterans) == root.parent / "history" / "history.sqlite"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert main(["--data", str(root), "score", "--model-dir", str(model_dir), "--now", NOW]) == 0
    assert store.load_fighters()[veterans[0]["fighter_id"]].elo is not None


def test_the_model_exports_rating_file_is_not_read(data, model_dir, tmp_path):
    root, store = data
    pd.DataFrame([{"fighter_id": fid, "rating": 2000.0, "peak": 2000.0, "n_bouts": 12, "n_ufc_bouts": 8,
                   "last_bout_date": "2026-10-01", "gender": "M"} for fid in store.load_fighters()]
                 ).to_parquet(model_dir / "fighter_rating.parquet")
    assert score_with(root, model_dir, history(tmp_path, unrelated())) == 0
    assert all(f.elo is None for f in store.load_fighters().values())


def test_best_win_is_written_as_a_json_object(data, model_dir, veterans, tmp_path):
    root, store = data
    score_with(root, model_dir, history(tmp_path, veterans))
    raw = json.loads(store.fighters_path.read_text())
    assert raw[veterans[0]["fighter_id"]]["best_win"]["opponent_id"] == veterans[1]["fighter_id"]
    assert set(raw[veterans[0]["fighter_id"]]["best_win"]) == {"date", "elo", "opponent", "opponent_id"}
    assert raw["nobody"]["best_win"] is None


@pytest.mark.parametrize("folder", ["a#b", "50%20off", "x?mode=rw"])
def test_a_history_store_path_with_uri_characters_is_opened_read_only(data, model_dir, veterans, tmp_path, folder):
    """The path goes into a `file:` URI: `#`, `%` and `?` in it must be quoted, not read as URI syntax."""
    root, store = data
    db = history(tmp_path / folder, veterans)
    before = db.read_bytes()
    assert score_with(root, model_dir, db) == 0
    assert store.load_fighters()[veterans[0]["fighter_id"]].elo is not None
    assert db.read_bytes() == before


def completed(eid: str, day: str, winner: str, loser: str) -> Event:
    return event(eid, day, [Fight(id=f"{eid}-1", red=winner, blue=loser, weight_class="Lightweight",
                                  result=Result(winner=winner, outcome="win", method="U-DEC", round=3))],
                 status="completed")


def test_a_completed_event_missing_from_the_history_store_fails(data, model_dir, veterans, tmp_path, capsys):
    """The store wasn't updated after the card: ratings would silently miss it."""
    root, store = data
    v0, v1 = veterans[0]["fighter_id"], veterans[1]["fighter_id"]
    store.save_event(completed("e-late", "2026-10-31", v0, v1))
    before = snapshot_files(root)
    assert score_with(root, model_dir, history(tmp_path, veterans)) == 1
    err = capsys.readouterr().err
    assert "completed events with no bouts in the history store: e-late" in err
    assert snapshot_files(root) == before


def test_a_completed_event_in_the_store_without_results_fails(data, model_dir, veterans, tmp_path):
    """Ingested before the results were in (the card page still showed it upcoming)."""
    root, store = data
    v0, v1 = veterans[0]["fighter_id"], veterans[1]["fighter_id"]
    store.save_event(completed("e-late", "2026-10-31", v0, v1))
    db = history(tmp_path, veterans)
    conn = connect(db)
    with conn:
        upsert_event(conn, event("e-late", "2026-10-31", [Fight(id="e-late-1", red=v0, blue=v1)]),
                     is_ufc=True, source="listing")
    conn.close()
    assert score_with(root, model_dir, db) == 1


def test_a_completed_event_in_the_store_passes_and_events_after_now_are_not_checked(data, model_dir, veterans,
                                                                                     tmp_path):
    root, store = data
    v0, v1 = veterans[0]["fighter_id"], veterans[1]["fighter_id"]
    store.save_event(completed("e-late", "2026-10-31", v0, v1))
    store.save_event(completed("e-later", "2026-11-08", v0, v1))  # after --now: not in this run's ratings
    db = history(tmp_path, veterans, [("2026-10-31", "late", v0, v1)])
    assert score_with(root, model_dir, db) == 0


def test_score_logs_the_stores_newest_completed_event_and_last_fetch_against_now(data, model_dir, veterans,
                                                                                tmp_path, capsys):
    root, _ = data
    db = history(tmp_path, veterans)
    conn = connect(db)
    with conn:
        upsert_event(conn, event("e-next", "2026-12-12", []), is_ufc=True, source="listing")  # upcoming: not newest
        conn.execute("INSERT INTO pages (url, path, sha256, fetched_at) VALUES "
                     "('u1', 'p1', 's', '2026-10-02T03:00:00Z'), ('u2', 'p2', 's', '2026-10-05T04:00:00Z'), "
                     "('u3', 'p3', 's', NULL)")
    conn.close()
    assert score_with(root, model_dir, db) == 0
    assert (f"score: history store {db}: newest completed event 2026-10-01, last page fetched "
            f"2026-10-05T04:00:00Z (now {NOW})") in capsys.readouterr().err.splitlines()


def test_the_store_line_says_unknown_without_fetch_times(data, model_dir, veterans, tmp_path, capsys):
    root, _ = data
    db = history(tmp_path, veterans)
    assert score_with(root, model_dir, db) == 0
    assert "newest completed event 2026-10-01, last page fetched unknown" in capsys.readouterr().err


def test_an_empty_completed_card_in_the_store_passes(data, model_dir, veterans, tmp_path):
    """A listed card with no bout (all cancelled) can't be "missing" forever: its completed row in the store is
    what scrape's backfill checks too."""
    root, store = data
    store.save_event(event("e-empty", "2026-10-31", [], status="completed"))
    db = history(tmp_path, veterans)
    conn = connect(db)
    with conn:
        upsert_event(conn, event("e-empty", "2026-10-31", [], status="completed"), is_ufc=True, source="listing")
    conn.close()
    assert score_with(root, model_dir, db) == 0


def test_a_listed_card_whose_bouts_have_no_outcome_yet_still_fails(data, model_dir, veterans, tmp_path, capsys):
    """Completed in the listing, results not on the card page yet: the ratings would miss it."""
    root, store = data
    v0, v1 = veterans[0]["fighter_id"], veterans[1]["fighter_id"]
    store.save_event(completed("e-late", "2026-10-31", v0, v1))
    db = history(tmp_path, veterans)
    conn = connect(db)
    with conn:
        upsert_event(conn, event("e-late", "2026-10-31", [Fight(id="e-late-1", red=v0, blue=v1)], status="completed"),
                     is_ufc=True, source="listing")
    conn.close()
    assert score_with(root, model_dir, db) == 1
    assert "e-late" in capsys.readouterr().err


def skip(db: Path, eid: str, reason: str) -> None:
    """Record `eid` in the store's `backfill_skips`, as scrape's backfill does when it gives up on a page."""
    conn = connect(db)
    with conn:
        conn.execute("INSERT INTO backfill_skips (event_id, tried_at, reason) VALUES (?, ?, ?)",
                     (eid, "2026-10-31T12:00:00Z", reason))
    conn.close()


def test_a_completed_event_the_backfill_skipped_warns_and_ratings_are_written(data, model_dir, veterans, tmp_path,
                                                                              capsys):
    """scrape's backfill gave up on it (page gone, or a bout without an outcome): failing every run would leave the
    last good generation stuck forever, so score names it and goes on."""
    root, store = data
    v0, v1 = veterans[0]["fighter_id"], veterans[1]["fighter_id"]
    store.save_event(completed("e-late", "2026-10-31", v0, v1))
    db = history(tmp_path, veterans)
    skip(db, "e-late", "HTTP 404")
    assert score_with(root, model_dir, db) == 0
    warnings = [line for line in capsys.readouterr().err.splitlines() if "WARNING" in line]
    assert len(warnings) == 1
    assert "e-late" in warnings[0] and "HTTP 404" in warnings[0]
    assert store.load_fighters()[v0].elo is not None


def test_a_skipped_event_relaxes_only_itself(data, model_dir, veterans, tmp_path, capsys):
    root, store = data
    v0, v1 = veterans[0]["fighter_id"], veterans[1]["fighter_id"]
    store.save_event(completed("e-late", "2026-10-31", v0, v1))
    store.save_event(completed("e-other", "2026-10-30", v0, v1))
    db = history(tmp_path, veterans)
    skip(db, "e-late", "HTTP 404")
    before = snapshot_files(root)
    assert score_with(root, model_dir, db) == 1
    assert "completed events with no bouts in the history store: e-other" in capsys.readouterr().err
    assert snapshot_files(root) == before


def test_a_store_without_the_backfill_skips_table_stays_strict(data, model_dir, veterans, tmp_path, capsys):
    """Stores made by `history ingest` before the table existed."""
    root, store = data
    v0, v1 = veterans[0]["fighter_id"], veterans[1]["fighter_id"]
    store.save_event(completed("e-late", "2026-10-31", v0, v1))
    db = history(tmp_path, veterans)
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("DROP TABLE backfill_skips")
    conn.close()
    assert score_with(root, model_dir, db) == 1
    err = capsys.readouterr().err
    assert "completed events with no bouts in the history store: e-late" in err
    assert "unusable" not in err


def test_a_store_without_the_backfill_skips_table_passes_when_complete(data, model_dir, veterans, tmp_path):
    root, _ = data
    db = history(tmp_path, veterans)
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("DROP TABLE backfill_skips")
    conn.close()
    assert score_with(root, model_dir, db) == 0


# --- data/rankings.json (the P4P and active lists /v1/rankings.json serves) ------------------------------------------

def five_bouts_each(veterans: list[dict]) -> list[tuple[str, str, str, str]]:
    """`history` extras that give every veteran 5 UFC bouts (the P4P minimum): 3 more for each pair."""
    ids = [v["fighter_id"] for v in veterans]
    return [(day, f"{day}-{i}", ids[i], ids[i + 1]) for day in ("2026-06-01", "2026-07-01", "2026-08-01")
            for i in (0, 2, 4)]


def rankings_of(root: Path) -> dict:
    return json.loads((root / "rankings.json").read_text())


def test_writes_rankings_json_as_the_contract_has_it(data, model_dir, veterans, tmp_path):
    root, store = data
    db = history(tmp_path, veterans, five_bouts_each(veterans))
    assert score_with(root, model_dir, db) == 0
    out = rankings_of(root)
    fighters = store.load_fighters()
    assert set(out) == {"as_of", "p4p", "active"}
    assert out["as_of"] == NOW[:10]
    assert set(out["p4p"]) == {"men", "women"} and out["p4p"]["women"] == []
    men = out["p4p"]["men"]
    assert [e["rank"] for e in men] == list(range(1, 7))
    for e in men:
        f = fighters[e["id"]]
        assert set(e) == {"rank", "id", "name", "elo", "division", "image"}
        assert (e["rank"], e["elo"], e["name"]) == (f.p4p_rank, f.elo, f.name)
        assert (e["division"], e["image"]) == ("Lightweight", None)
    assert {e["id"] for e in men} == {v["fighter_id"] for v in veterans}


def test_rankings_active_lists_every_active_fighter_with_a_ufc_bout_best_first(data, model_dir, veterans, tmp_path):
    root, store = data
    ids = [v["fighter_id"] for v in veterans]
    db = history(tmp_path, veterans, [*five_bouts_each(veterans), ("2024-01-01", "old", "oldA", "oldB")])
    assert score_with(root, model_dir, db) == 0
    active = rankings_of(root)["active"]
    fighters = store.load_fighters()
    assert sorted(r["id"] for r in active) == sorted(ids)  # oldA/oldB fought in 2024: inactive
    assert [r["elo"] for r in active] == sorted((r["elo"] for r in active), reverse=True)
    for r in active:
        assert set(r) == {"id", "name", "elo", "division", "p4p_rank", "provisional"}
        f = fighters[r["id"]]
        assert (r["elo"], r["p4p_rank"], r["provisional"]) == (f.elo, f.p4p_rank, f.elo_provisional)


def test_rankings_cover_fighters_who_are_not_in_fighters_json(data, model_dir, veterans, tmp_path):
    root, store = data
    stranger = [(f"2026-0{m}-15", f"s{m}", "sA", "sB") for m in range(1, 6)]
    db = history(tmp_path, veterans, stranger)
    assert score_with(root, model_dir, db) == 0
    men = {e["id"]: e for e in rankings_of(root)["p4p"]["men"]}
    assert men["sA"]["name"] == "sA" and men["sA"]["division"] == "Lightweight"  # no name in the store: the id
    assert "sA" not in store.load_fighters()


def test_rankings_image_is_set_when_the_headshot_exists(data, model_dir, veterans, tmp_path):
    root, _ = data
    v0 = veterans[0]["fighter_id"]
    (root / "images").mkdir()
    (root / "images" / f"{v0}.webp").write_bytes(b"webp")
    (root / "images" / f"{veterans[1]['fighter_id']}.png").write_bytes(b"png")  # not webp: no image
    db = history(tmp_path, veterans, five_bouts_each(veterans))
    assert score_with(root, model_dir, db) == 0
    out = rankings_of(root)
    assert {e["id"]: e["image"] for e in out["p4p"]["men"]} == {
        v["fighter_id"]: (f"images/{v0}.webp" if v["fighter_id"] == v0 else None) for v in veterans}
    assert all("image" not in r for r in out["active"])


def test_an_unchanged_run_rewrites_identical_rankings(data, model_dir, veterans, tmp_path):
    root, _ = data
    db = history(tmp_path, veterans, five_bouts_each(veterans))
    score_with(root, model_dir, db)
    before = (root / "rankings.json").read_text()
    assert score_with(root, model_dir, db) == 0
    assert (root / "rankings.json").read_text() == before


def test_rankings_are_dated_and_decayed_to_now(data, model_dir, veterans, tmp_path):
    root, _ = data
    db = history(tmp_path, veterans, five_bouts_each(veterans))
    score_with(root, model_dir, db, now="2026-11-01T06:00:00Z")
    first = rankings_of(root)
    assert score_with(root, model_dir, db, now="2027-08-01T06:00:00Z") == 0
    later = rankings_of(root)
    assert (first["as_of"], later["as_of"]) == ("2026-11-01", "2027-08-01")
    assert later["p4p"]["men"] == [] and later["active"] == []  # nobody fought in the last 365 days


def test_no_history_writes_no_rankings(data, model_dir):
    root, _ = data
    assert score(root, model_dir) == 0
    assert not (root / "rankings.json").exists()


def test_no_history_leaves_an_existing_rankings_file_alone(data, model_dir):
    """Like the fighters' rating fields: --no-history neither refreshes nor removes what a full run wrote."""
    root, _ = data
    (root / "rankings.json").write_text('{"as_of": "2026-10-01", "p4p": {"men": [], "women": []}, "active": []}\n')
    before = (root / "rankings.json").read_bytes()
    assert score(root, model_dir) == 0
    assert (root / "rankings.json").read_bytes() == before


def test_a_failed_history_run_leaves_rankings_as_they_were(data, model_dir, tmp_path):
    root, _ = data
    (root / "rankings.json").write_text('{"as_of": "2026-10-01", "p4p": {"men": [], "women": []}, "active": []}\n')
    before = (root / "rankings.json").read_bytes()
    assert score_with(root, model_dir, tmp_path / "nope.sqlite") == 1
    assert (root / "rankings.json").read_bytes() == before
