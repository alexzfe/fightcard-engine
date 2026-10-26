"""fightcard-service run: gate → scrape → history → segments → images → score → publish, with fake stages."""

from __future__ import annotations

import fcntl
import itertools
import re
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from fightcard.service import runner
from fightcard.service.runner import Config

REPO = Path(__file__).resolve().parents[2]
ORDER = ["scrape", "segments", "images", "score", "publish"]


def config(tmp_path: Path, **overrides) -> Config:
    values = dict(root=tmp_path / "srv", fetch=True, fightcard="fightcard", model_dir=Path("/app/model"),
                  gate_script=REPO / "scripts" / "should_run.py", python=sys.executable)
    values.update(overrides)
    return Config(**values)


class Recorder:
    """Fake stage executor: records each stage's argv and returns the scripted exit code."""

    def __init__(self, codes: dict[str, int] | None = None):
        self.codes = codes or {}
        self.calls: list[list[str]] = []
        self.timeouts: dict[str, float] = {}

    def __call__(self, argv: list[str], *, timeout: float, **_) -> int:
        self.calls.append(argv)
        self.timeouts[stage_of(argv)] = timeout
        return self.codes.get(stage_of(argv), 0)

    @property
    def stages(self) -> list[str]:
        return [stage_of(argv) for argv in self.calls]


def stage_of(argv: list[str]) -> str:
    return next(a for a in argv if a in ORDER)


def go(cfg: Config, execute=None, gate=None, event="manual", cron=""):
    lines: list[str] = []
    ticks = itertools.count(0, 0.5)
    code = runner.run_once(cfg, event=event, cron=cron, execute=execute or Recorder(),
                           gate=gate or (lambda *_: (True, "ok")), out=lines.append, clock=lambda: next(ticks),
                           run_id="r1")
    return code, lines


def fields(line: str) -> dict[str, str]:
    return {k: v.strip('"') for k, v in re.findall(r'(\w+)=("[^"]*"|\S+)', line)}


def step_lines(lines: list[str]) -> dict[str, dict[str, str]]:
    return {f["step"]: f for f in map(fields, lines) if "step" in f}


# Order and arguments.

def test_runs_every_stage_in_order(tmp_path):
    rec = Recorder()
    code, lines = go(config(tmp_path), rec)
    assert code == 0
    assert rec.stages == ORDER
    assert list(step_lines(lines)) == ORDER  # no separate history step: scrape upserts the store itself


def test_stages_share_the_state_data_dir_and_publish_to_the_root(tmp_path):
    cfg = config(tmp_path)
    rec = Recorder()
    go(cfg, rec)
    data = str(cfg.root / "state" / "data")
    for argv in rec.calls:
        assert argv[:3] == ["fightcard", "--data", data], argv
    by_stage = {stage_of(a): a for a in rec.calls}
    assert by_stage["publish"][3:] == ["publish", "--out", str(cfg.root)]
    assert by_stage["score"][3:] == ["score", "--model-dir", "/app/model", "--history-db",
                                     str(cfg.root / "state" / "history" / "history.sqlite")]
    assert by_stage["scrape"][3:] == ["scrape", "--cookie-file",
                                      str(cfg.root / "state" / "cache" / "fightcard" / "ufcstats_cookie.json")]


def test_publish_root_matches_the_nginx_config(tmp_path):
    conf = (REPO / "deploy" / "nginx" / "fightcard-engine.conf").read_text()
    assert f"alias {runner.ROOT}/current/" in conf
    assert Config.from_env({}).root == Path(runner.ROOT)


def test_scrape_and_score_share_the_history_store_beside_data(tmp_path):
    cfg = config(tmp_path)
    assert cfg.history_db == cfg.data.parent / "history" / "history.sqlite"  # scrape's and score's default
    rec = Recorder()
    go(cfg, rec)
    scrape = rec.calls[rec.stages.index("scrape")]
    assert "--no-history" not in scrape and "history" not in rec.stages
    assert cfg.history_db.parent.is_dir()


# Failures.

@pytest.mark.parametrize("failing", ["scrape", "segments", "images", "score", "publish"])
def test_stops_at_the_first_failing_stage_and_returns_its_code(tmp_path, failing):
    rec = Recorder({failing: 3})
    code, lines = go(config(tmp_path), rec)
    assert code == 3
    assert rec.stages[-1] == failing
    assert "publish" not in rec.stages or failing == "publish"
    steps = step_lines(lines)
    assert steps[failing]["status"] == "failed" and steps[failing]["exit"] == "3"
    end = fields(lines[-1])
    assert end["status"] == "failed" and end["failed_step"] == failing and end["exit"] == "3"


def test_a_stage_that_cannot_start_counts_as_a_failure(tmp_path):
    def execute(argv, **_):
        raise FileNotFoundError(argv[0])
    code, lines = go(config(tmp_path), execute)
    assert code == 127
    assert step_lines(lines)["scrape"]["status"] == "failed"


# Logs.

def test_one_line_per_stage_with_exit_code_and_duration(tmp_path):
    _, lines = go(config(tmp_path))
    assert all(line.startswith("fightcard-run run=r1 ") for line in lines)
    steps = step_lines(lines)
    for name in ["scrape", "segments", "images", "score", "publish"]:
        assert steps[name]["status"] == "ok" and steps[name]["exit"] == "0"
        assert re.fullmatch(r"\d+\.\d\d", steps[name]["duration_s"]), steps[name]
    start, end = fields(lines[0]), fields(lines[-1])
    assert start["status"] == "start" and start["event"] == "manual" and start["fetch"] == "on"
    assert end["status"] == "ok" and "duration_s" in end


# Gate.

def test_a_scheduled_run_asks_the_gate_with_its_cron(tmp_path):
    seen = []

    def gate(cfg, event, cron):
        seen.append((event, cron))
        return False, "no event today or yesterday"

    rec = Recorder()
    code, lines = go(config(tmp_path), rec, gate, event="schedule", cron="17 * * * *")
    assert code == 0 and rec.calls == []
    assert seen == [("schedule", "17 * * * *")]
    gate_line = step_lines(lines)["gate"]
    assert gate_line["status"] == "skip" and gate_line["reason"] == "no event today or yesterday"
    assert fields(lines[-1])["status"] == "skipped"


def test_the_gate_letting_a_run_through_logs_its_reason(tmp_path):
    rec = Recorder()
    code, lines = go(config(tmp_path), rec, lambda *_: (True, "daily run"), event="schedule", cron="0 6 * * *")
    assert code == 0 and rec.stages[-1] == "publish"
    assert step_lines(lines)["gate"] == {**step_lines(lines)["gate"], "status": "run", "reason": "daily run"}


def test_a_broken_gate_fails_the_run_before_any_stage(tmp_path):
    def gate(*_):
        raise ValueError("data/index.json: bad json")
    rec = Recorder()
    code, lines = go(config(tmp_path), rec, gate, event="schedule", cron="17 * * * *")
    assert code == 1 and rec.calls == []
    assert fields(lines[-1])["failed_step"] == "gate"


def test_the_real_gate_script_decides_from_the_data_dir(tmp_path):
    cfg = config(tmp_path)
    cfg.data.mkdir(parents=True)
    (cfg.data / "index.json").write_text('{"upcoming": [], "recent": []}')
    assert runner.script_gate(cfg, "schedule", "17 * * * *") == (False, "no event today or yesterday")
    assert runner.script_gate(cfg, "schedule", "0 6 * * *") == (True, "daily run")


def test_the_real_gate_script_failing_raises(tmp_path):
    cfg = config(tmp_path)
    cfg.data.mkdir(parents=True)
    (cfg.data / "index.json").write_text("{not json")
    with pytest.raises(RuntimeError, match="should_run.py"):
        runner.script_gate(cfg, "schedule", "17 * * * *")


# Fetch off (staging, the container test).

def test_fetch_off_skips_the_network_stages(tmp_path):
    rec = Recorder()
    code, lines = go(config(tmp_path, fetch=False), rec)
    assert code == 0 and rec.stages == ["score", "publish"]
    steps = step_lines(lines)
    for name in ["scrape", "segments", "images"]:
        assert (steps[name]["status"], steps[name]["reason"]) == ("skipped", "fetch-off")
    assert fields(lines[0])["fetch"] == "off"


@pytest.mark.parametrize("env,fetch", [
    ({}, True),
    ({"FIGHTCARD_FETCH": "off"}, False),
    ({"FIGHTCARD_FETCH": "on"}, True),
    ({"FIGHTCARD_HOST": "ufc-data.xlobax.com", "FIGHTCARD_FETCH_HOST": "ufc-data.xlobax.com"}, True),
    ({"FIGHTCARD_HOST": "ufc-data-staging.xlobax.com", "FIGHTCARD_FETCH_HOST": "ufc-data.xlobax.com"}, False),
    ({"FIGHTCARD_FETCH": "off", "FIGHTCARD_HOST": "a", "FIGHTCARD_FETCH_HOST": "a"}, False),
])
def test_fetch_comes_from_the_environment(env, fetch):
    assert Config.from_env(env).fetch is fetch


def test_config_reads_paths_from_the_environment(tmp_path):
    cfg = Config.from_env({"FIGHTCARD_ROOT": str(tmp_path), "FIGHTCARD_MODEL_DIR": "/m",
                           "FIGHTCARD_GATE_SCRIPT": "/g.py"})
    assert (cfg.root, cfg.model_dir, cfg.gate_script) == (tmp_path, Path("/m"), Path("/g.py"))
    assert cfg.lock == tmp_path / "state" / "run.lock"
    assert cfg.history_db == tmp_path / "state" / "history" / "history.sqlite"


# Lock.

def test_a_second_run_while_one_holds_the_lock_does_nothing(tmp_path):
    cfg = config(tmp_path)
    cfg.lock.parent.mkdir(parents=True)
    with open(cfg.lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rec = Recorder()
        code, lines = go(cfg, rec)
    assert code == runner.LOCKED and rec.calls == []
    assert fields(lines[-1])["status"] == "locked"


def test_the_lock_is_held_during_the_stages_and_released_after(tmp_path):
    cfg = config(tmp_path)
    probes = []

    def execute(argv, **_):
        with open(cfg.lock, "w") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                probes.append("free")
            except BlockingIOError:
                probes.append("held")
        return 0

    go(cfg, execute)
    assert probes and set(probes) == {"held"}
    with open(cfg.lock, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released: doesn't raise


def test_a_run_creates_the_state_dirs(tmp_path):
    cfg = config(tmp_path)
    go(cfg)
    assert cfg.data.is_dir() and cfg.cache.is_dir()


# The real subprocess executor and the CLI.

def test_execute_runs_the_command_and_returns_its_exit_code():
    assert runner.execute([sys.executable, "-c", "import sys; sys.exit(4)"], timeout=30) == 4


def test_each_stage_gets_its_own_timeout(tmp_path):
    rec = Recorder()
    go(config(tmp_path), rec)
    assert rec.timeouts == runner.TIMEOUTS


def test_cli_run_uses_the_environment(tmp_path, monkeypatch):
    from fightcard.service import __main__ as cli
    seen = {}

    def fake_run_once(cfg, **kwargs):
        seen.update(cfg=cfg, **kwargs)
        return 0

    monkeypatch.setattr(runner, "run_once", fake_run_once)
    monkeypatch.setenv("FIGHTCARD_ROOT", str(tmp_path))
    monkeypatch.setenv("FIGHTCARD_FETCH", "off")
    previous = signal.getsignal(signal.SIGTERM)
    try:
        assert cli.main(["run", "--event", "schedule", "--cron", "0 6 * * *"]) == 0
        assert signal.getsignal(signal.SIGTERM) is runner.terminate  # so a TERM ends the stage first
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert seen["cfg"].root == tmp_path and seen["cfg"].fetch is False
    assert (seen["event"], seen["cron"]) == ("schedule", "0 6 * * *")


def test_console_script_is_installed():
    exe = Path(sys.executable).with_name("fightcard-service")
    result = subprocess.run([str(exe), "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "run" in result.stdout and "serve" in result.stdout

