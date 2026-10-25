"""`fightcard --data data score`: tiers, no-history and staleness guards, freezing, index.model."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from fightcard.cli import main
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
    return main(["--data", str(root), "score", "--model-dir", str(model_dir), "--now", now])


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
    assert store.load_index().model.model_dump() == {"as_of": m["as_of"], "version": m["version"]}


def test_missing_model_dir_fails(data, tmp_path, capsys):
    root, _ = data
    assert score(root, tmp_path / "nope") == 1
    assert "model" in capsys.readouterr().err


def test_default_model_dir_and_now(data, monkeypatch):
    """Without flags: model/ relative to the cwd, and the current UTC time."""
    root, store = data
    monkeypatch.chdir(Path(__file__).resolve().parents[3])
    assert main(["--data", str(root), "score"]) == 0
    assert tiers(store, "future")["ok"] in ("low", "medium", "high")


def test_unchanged_run_rewrites_identical_files(data, model_dir):
    root, store = data
    score(root, model_dir)
    before = (root / "events" / "future.json").read_text()
    score(root, model_dir, now="2026-11-02T06:00:00Z")
    assert (root / "events" / "future.json").read_text() == before


def test_writes_current_elo_into_fighters_json(data, model_dir, veterans, capsys):
    root, store = data
    assert score(root, model_dir) == 0
    fighters = store.load_fighters()
    for v in veterans:
        assert fighters[v["fighter_id"]].elo == round(v["elo"])
    assert fighters["nobody"].elo is None
    assert sum(f.elo is None for f in fighters.values()) == 2  # nobody and the rookie
    assert "6 fighters with an Elo" in capsys.readouterr().err
    before = store.fighters_path.read_text()
    score(root, model_dir)
    assert store.fighters_path.read_text() == before
