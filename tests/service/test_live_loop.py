"""The fight-night fast loop (docs/SERVICE.md, "Live refresh"): while an indexed event dated today or yesterday isn't
fully resulted, serve starts a light `run --event live` every FIGHTCARD_LIVE_MINUTES (5): scrape --live → score →
publish, with score and publish skipped when the scrape saw nothing new. A failed run backs it off to LIVE_BACKOFF.
Off where fetching is off (staging), and with FIGHTCARD_LIVE_MINUTES=off. Hourly and daily runs are unchanged."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from test_runner import Recorder, fields, go, step_lines
from test_runner import config as run_config
from test_serve import RUN, World

from fightcard.models import Event, Fight, Index, Result
from fightcard.service import live, runner, serve
from fightcard.service.__main__ import main as service_main
from fightcard.service.runner import Config
from fightcard.store import Store

REPO = Path(__file__).resolve().parents[2]
WIN = Result(winner="a", outcome="win", method="KO/TKO")
LIVE = RUN + ["--event", "live"]
HOURLY = RUN + ["--event", "schedule", "--cron", "17 * * * *"]


def fight_night(data: Path, day: str = "2026-10-31", results=(WIN, None)) -> None:
    store = Store(data)
    store.save_event(Event(id="e1", slug="e1", name="UFC Fight Night", date=day, status="completed",
                           fights=[Fight(id=f"f{i}", red="a", blue="b", result=r) for i, r in enumerate(results)]))
    store.save_index(Index(generated_at="2026-10-31T23:17:00Z", upcoming=[], recent=["e1"]))


# --- Config -----------------------------------------------------------------------------------------------------


PROD = {"FIGHTCARD_HOST": "ufc-data.xlobax.com", "FIGHTCARD_FETCH_HOST": "ufc-data.xlobax.com"}
STAGING = {"FIGHTCARD_HOST": "ufc-data-staging.xlobax.com", "FIGHTCARD_FETCH_HOST": "ufc-data.xlobax.com"}


@pytest.mark.parametrize("env,every", [
    ({}, timedelta(minutes=5)),
    (PROD, timedelta(minutes=5)),
    (STAGING, None),  # no fetching there: a second scraper on the box's one IP would double ufcstats' load
    ({"FIGHTCARD_FETCH": "off"}, None),
    ({**PROD, "FIGHTCARD_LIVE_MINUTES": "2"}, timedelta(minutes=2)),
    ({**PROD, "FIGHTCARD_LIVE_MINUTES": "off"}, None),
    ({**PROD, "FIGHTCARD_LIVE_MINUTES": "0"}, None),
    ({**STAGING, "FIGHTCARD_LIVE_MINUTES": "5"}, None),
])
def test_the_live_interval_comes_from_the_environment(env, every):
    assert Config.from_env(env).live_every == every


@pytest.mark.parametrize("value", ["-1", "x", "1.5", "61"])
def test_a_bad_live_interval_is_refused(value):
    with pytest.raises(ValueError, match="FIGHTCARD_LIVE_MINUTES"):
        Config.from_env({"FIGHTCARD_LIVE_MINUTES": value})


def test_the_default_config_has_no_live_loop(tmp_path):
    assert run_config(tmp_path).live_every is None


# --- runner: run --event live ------------------------------------------------------------------------------------


def live_run(tmp_path, rec=None, fetch=True, now=datetime(2026, 10, 31, 23, 5, tzinfo=UTC)):
    cfg = run_config(tmp_path, fetch=fetch)
    rec = rec or Recorder()
    code, lines = go(cfg, rec, event="live", now=lambda: now)
    return cfg, rec, code, lines


def test_a_live_run_is_scrape_live_score_publish(tmp_path):
    fight_night(run_config(tmp_path).data)
    cfg, rec, code, lines = live_run(tmp_path)
    assert code == 0
    assert rec.stages == ["scrape", "score", "publish"]  # no segments, images or model pull
    by_stage = dict(zip(rec.stages, rec.calls))
    assert by_stage["scrape"][3:] == ["scrape", "--live", "--cookie-file",
                                      str(cfg.root / "state" / "cache" / "fightcard" / "ufcstats_cookie.json")]
    assert by_stage["score"][3:5] == ["score", "--model-dir"]
    assert by_stage["publish"][3:] == ["publish", "--out", str(cfg.root)]
    gate = step_lines(lines)["gate"]
    assert gate["status"] == "run" and "e1" in gate["reason"]


def test_nothing_new_skips_score_and_publish(tmp_path):
    fight_night(run_config(tmp_path).data)
    _, rec, code, lines = live_run(tmp_path, Recorder({"scrape": live.UNCHANGED}))
    assert code == 0
    assert rec.stages == ["scrape"]
    assert step_lines(lines)["scrape"]["status"] == "unchanged"
    assert fields(lines[-1])["status"] == "unchanged"


def test_a_failing_live_scrape_fails_the_run(tmp_path):
    fight_night(run_config(tmp_path).data)
    _, rec, code, _ = live_run(tmp_path, Recorder({"scrape": 1}))
    assert code == 1
    assert rec.stages == ["scrape"]


def test_unchanged_means_nothing_to_the_hourly_run(tmp_path):
    # only a live run's scrape may say "unchanged"; for the full pipeline an exit 3 is a failure as before
    _, lines = go(run_config(tmp_path), Recorder({"scrape": live.UNCHANGED}))
    assert fields(lines[-1])["status"] == "failed"


def test_with_no_live_event_a_live_run_runs_nothing(tmp_path):
    fight_night(run_config(tmp_path).data, day="2026-10-20")
    _, rec, code, lines = live_run(tmp_path)
    assert code == 0
    assert rec.stages == []
    assert step_lines(lines)["gate"]["status"] == "skip"


def test_where_fetching_is_off_a_live_run_runs_nothing(tmp_path):
    fight_night(run_config(tmp_path).data)
    _, rec, code, lines = live_run(tmp_path, fetch=False)
    assert code == 0
    assert rec.stages == []
    assert step_lines(lines)["gate"]["reason"] == "fetch-off"


def test_a_live_run_s_scrape_has_a_short_timeout(tmp_path):
    # one page, not the whole site: a hang shouldn't hold the lock past the next hourly slot
    fight_night(run_config(tmp_path).data)
    _, rec, _, _ = live_run(tmp_path)
    assert rec.timeouts["scrape"] == runner.LIVE_SCRAPE_TIMEOUT < runner.TIMEOUTS["scrape"]


def test_the_cli_accepts_the_live_event(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "run_once", lambda cfg, event, cron: seen.setdefault("event", event) and 0)
    service_main(["run", "--event", "live"])
    assert seen["event"] == "live"


# --- serve: the fast loop -------------------------------------------------------------------------------------------


def serve_config(tmp_path, every=timedelta(minutes=5)) -> Config:
    cfg = Config(root=tmp_path / "srv", fetch=True, fightcard="fightcard", model_dir=Path("model"),
                 gate_script=REPO / "scripts" / "should_run.py", python=sys.executable, live_every=every)
    (cfg.root / "generations" / "x").mkdir(parents=True)
    (cfg.root / "current").symlink_to("generations/x")
    return cfg


def starts(world: World, argv: list[str]) -> list[str]:
    return [t.strftime("%H:%M") for t, p in zip(world.spawned_at[1:], world.procs[1:]) if p.argv == argv]


def test_on_fight_night_a_live_run_starts_every_five_minutes(tmp_path):
    cfg = serve_config(tmp_path)
    fight_night(cfg.data)
    world = World("2026-10-31 23:00:00", steps=40)  # 20 minutes, 23:00-23:20
    world.serve(cfg)
    assert starts(world, LIVE) == ["23:00", "23:05", "23:10", "23:15"]
    assert starts(world, HOURLY) == ["23:17"]  # the hourly slot as before


def test_off_fight_days_no_live_run_starts(tmp_path):
    cfg = serve_config(tmp_path)
    fight_night(cfg.data, day="2026-10-20")
    world = World("2026-10-31 23:00:00", steps=40)
    world.serve(cfg)
    assert starts(world, LIVE) == []


def test_once_every_result_is_in_no_live_run_starts(tmp_path):
    cfg = serve_config(tmp_path)
    fight_night(cfg.data, results=(WIN, WIN))
    world = World("2026-10-31 23:00:00", steps=40)
    world.serve(cfg)
    assert starts(world, LIVE) == []


def test_the_loop_stops_when_a_run_brings_the_last_result_in(tmp_path):
    cfg = serve_config(tmp_path)
    fight_night(cfg.data)
    world = World("2026-10-31 23:00:00", steps=40)
    world.on_spawn = lambda argv: argv == LIVE and world.ticks >= 10 and fight_night(cfg.data, results=(WIN, WIN))
    world.serve(cfg)
    assert starts(world, LIVE) == ["23:00", "23:05"]


def test_with_the_loop_off_no_live_run_starts(tmp_path):
    cfg = serve_config(tmp_path, every=None)
    fight_night(cfg.data)
    world = World("2026-10-31 23:00:00", steps=40)
    world.serve(cfg)
    assert starts(world, LIVE) == []


def test_a_failed_run_backs_the_loop_off(tmp_path):
    cfg = serve_config(tmp_path)
    fight_night(cfg.data)
    world = World("2026-10-31 23:00:00", steps=80, run_codes=[1])  # the first live run fails, the rest succeed
    world.serve(cfg)
    assert starts(world, LIVE) == ["23:00", "23:15", "23:20", "23:25", "23:30", "23:35"]
    assert any(fields(line).get("status") == "backoff" for line in world.lines)
    assert serve.LIVE_BACKOFF == timedelta(minutes=15)


def test_a_live_slot_during_another_run_is_skipped_not_queued(tmp_path):
    cfg = serve_config(tmp_path)
    fight_night(cfg.data)
    world = World("2026-10-31 23:10:00", steps=32, run_lifetime=14)  # every run takes 7 minutes
    world.serve(cfg)
    assert [p.argv[4] for p in world.procs[1:]] == ["live", "schedule", "live"]
    assert starts(world, LIVE) == ["23:10", "23:25"]


def test_unreadable_data_does_not_stop_serve(tmp_path):
    cfg = serve_config(tmp_path)
    cfg.data.mkdir(parents=True)
    (cfg.data / "index.json").write_text("{not json")
    world = World("2026-10-31 23:00:00", steps=20)
    assert world.serve(cfg) == 0
    assert starts(world, LIVE) == []
    assert any(fields(line).get("event") == "live" and fields(line).get("status") == "failed" for line in world.lines)


def test_due_events_reads_the_index_and_event_files(tmp_path):
    fight_night(tmp_path)
    assert live.due_events(tmp_path, date(2026, 10, 31)) == ["e1"]
