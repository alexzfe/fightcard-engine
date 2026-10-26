"""Supervision with real processes: stage timeouts, process groups, SIGTERM, the lock, zombies and shutdown.

The `fightcard` binary is tests/service/fake_stage.py, which sleeps, ignores SIGTERM or starts a grandchild when
told to, and reports its pid, process group and niceness."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

from fightcard.service import runner
from fightcard.service.runner import Config

REPO = Path(__file__).resolve().parents[2]
FAKE = Path(__file__).with_name("fake_stage.py")


def fake_bin(tmp: Path) -> Path:
    exe = tmp / "bin" / "fightcard"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    exe.chmod(0o755)
    return exe


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A config whose stages are fake_stage.py, reporting into tmp_path/fake."""
    (tmp_path / "fake").mkdir()
    monkeypatch.setenv("FAKE_DIR", str(tmp_path / "fake"))
    return Config(root=tmp_path / "srv", fetch=True, fightcard=str(fake_bin(tmp_path)), model_dir=Path("model"),
                  gate_script=REPO / "scripts" / "should_run.py", python=sys.executable)


def report(cfg: Config, stage: str) -> dict:
    return json.loads((Path(os.environ["FAKE_DIR"]) / f"{stage}.json").read_text())


def proc_state(pid: int) -> str | None:
    """The /proc state letter (R, S, Z, …), or None once the pid is gone."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return None


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def fields(line: str) -> dict[str, str]:
    return {k: v.strip('"') for k, v in re.findall(r'(\w+)=("[^"]*"|\S+)', line)}


def run(cfg: Config, **kwargs) -> tuple[int, list[dict[str, str]]]:
    lines: list[str] = []
    code = runner.run_once(cfg, out=lines.append, run_id="r1", **kwargs)
    return code, [fields(line) for line in lines]


# Finding 1: a per-stage timeout, enforced on the stage's whole process group.

def test_the_timeouts_are_per_stage_and_in_one_place():
    assert runner.TIMEOUTS == {"scrape": 30 * 60, "segments": 15 * 60, "images": 15 * 60, "score": 10 * 60,
                               "publish": 10 * 60}
    assert set(runner.TIMEOUTS) == {step.name for step in runner.steps(Config.from_env({}))}


def test_a_stage_past_its_timeout_is_killed_with_its_process_group(fake, monkeypatch):
    monkeypatch.setattr(runner, "TIMEOUTS", {**runner.TIMEOUTS, "scrape": 1})
    monkeypatch.setattr(runner, "KILL_GRACE", 0.5)
    monkeypatch.setenv("FAKE_SLEEP_SCRAPE", "60")
    monkeypatch.setenv("FAKE_CHILD_SCRAPE", "1")
    t0 = time.monotonic()
    code, lines = run(fake)
    assert time.monotonic() - t0 < 10
    assert code == runner.TIMED_OUT
    step = next(f for f in lines if f.get("step") == "scrape")
    assert step["status"] == "timeout" and step["timeout_s"] == "1"
    assert lines[-1]["status"] == "failed" and lines[-1]["failed_step"] == "scrape"
    stage = report(fake, "scrape")
    assert wait_until(lambda: proc_state(stage["pid"]) in (None, "Z"))
    assert wait_until(lambda: proc_state(stage["child"]) in (None, "Z")), "the grandchild outlived its stage"
    assert not (Path(os.environ["FAKE_DIR"]) / "segments.json").exists()


def test_a_stage_that_ignores_sigterm_is_killed_after_the_grace(fake, monkeypatch):
    monkeypatch.setattr(runner, "TIMEOUTS", {**runner.TIMEOUTS, "scrape": 0.5})
    monkeypatch.setattr(runner, "KILL_GRACE", 0.5)
    monkeypatch.setenv("FAKE_SLEEP_SCRAPE", "60")
    monkeypatch.setenv("FAKE_IGNORE_TERM_SCRAPE", "1")
    t0 = time.monotonic()
    code, _ = run(fake)
    assert code == runner.TIMED_OUT
    assert 1.0 <= time.monotonic() - t0 < 10
    assert wait_until(lambda: proc_state(report(fake, "scrape")["pid"]) in (None, "Z"))


def test_each_stage_runs_in_its_own_process_group_at_nice_10(fake):
    code, _ = run(fake)
    assert code == 0
    mine = os.nice(0)
    for stage in ("scrape", "score", "publish"):
        r = report(fake, stage)
        assert r["pgid"] == r["pid"] != os.getpgid(0), stage
        assert r["nice"] == min(mine + runner.NICE, 19), stage
    assert runner.NICE == 10
