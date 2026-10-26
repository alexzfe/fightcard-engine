"""Supervision with real processes: stage timeouts, process groups, SIGTERM, the lock, zombies and shutdown.

The `fightcard` binary is tests/service/fake_stage.py, which sleeps, ignores SIGTERM or starts a grandchild when
told to, and reports its pid, process group and niceness."""

from __future__ import annotations

import ctypes
import fcntl
import json
import os
import re
import signal
import subprocess
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
    monkeypatch.setenv("FAKE_CHILD_SCRAPE", "300")
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


# Finding 4: SIGTERM reaches the stage, the lock outlives no stage, and PID 1 reaps orphans.

def start_run(cfg: Config, **env: str) -> subprocess.Popen:
    """`fightcard-service run` as a real process, over the fake stages."""
    return subprocess.Popen([sys.executable, "-m", "fightcard.service", "run"], stdout=subprocess.PIPE, text=True,
                            env={**os.environ, "FIGHTCARD_ROOT": str(cfg.root), "FIGHTCARD_BIN": cfg.fightcard,
                                 "FIGHTCARD_FETCH": "on", **env})


def started(stage: str) -> Path:
    path = Path(os.environ["FAKE_DIR"]) / f"{stage}.json"
    assert wait_until(lambda: path.exists() and path.read_text(), timeout=20), f"{stage} never started"
    return path


def lock_is_free(cfg: Config) -> bool:
    with open(cfg.lock, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False


def test_sigterm_to_a_run_ends_its_stage_first_then_the_run(fake):
    run_proc = start_run(fake, FAKE_SLEEP_SCRAPE="60", FAKE_CHILD_SCRAPE="300")
    started("scrape")
    stage = report(fake, "scrape")
    run_proc.send_signal(signal.SIGTERM)
    out, _ = run_proc.communicate(timeout=10)
    assert run_proc.returncode == runner.TERMINATED == 143
    # By the time the run has exited, its stage has too, so the lock it drops guards nothing still running.
    assert proc_state(stage["pid"]) in (None, "Z")
    assert wait_until(lambda: proc_state(stage["child"]) in (None, "Z"))
    assert lock_is_free(fake)
    end = fields(out.splitlines()[-1])
    assert end["status"] == "terminated" and end["failed_step"] == "scrape"


def test_a_stage_holds_the_lock_even_if_its_run_is_killed(fake):
    run_proc = start_run(fake, FAKE_SLEEP_SCRAPE="60")
    started("scrape")
    stage = report(fake, "scrape")
    run_proc.kill()
    run_proc.wait()
    try:
        assert proc_state(stage["pid"]) not in (None, "Z")
        assert not lock_is_free(fake), "the lock was released while scrape still runs"
    finally:
        os.killpg(stage["pgid"], signal.SIGKILL)
    assert wait_until(lambda: lock_is_free(fake))


SUBREAPER = """
import ctypes, sys
ctypes.CDLL(None, use_errno=True).prctl(36, 1)  # PR_SET_CHILD_SUBREAPER: stand in for PID 1
from fightcard.service import serve
serve.NGINX = [sys.executable, "-c", "import time; time.sleep(300)"]
sys.exit(serve.main())
"""


def children(pid: int) -> dict[int, str]:
    """pid -> state of every process whose parent is `pid`."""
    found = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            text = stat.read_text()
        except (FileNotFoundError, ProcessLookupError):
            continue
        state, ppid = text.rsplit(")", 1)[1].split()[:2]
        if int(ppid) == pid:
            found[int(stat.parent.name)] = state
    return found


@pytest.mark.skipif(not hasattr(ctypes.CDLL(None), "prctl"), reason="needs Linux prctl")
def test_serve_reaps_the_orphans_of_a_killed_run(fake):
    # Nothing published, so serve starts a run at once; its scrape outlives the run, which is killed.
    serve_proc = subprocess.Popen(
        [sys.executable, "-c", SUBREAPER], stdout=subprocess.DEVNULL,
        env={**os.environ, "FIGHTCARD_ROOT": str(fake.root), "FIGHTCARD_BIN": fake.fightcard, "FIGHTCARD_FETCH": "on",
             "FAKE_SLEEP_SCRAPE": "1", "FAKE_EXIT_SCRAPE": "1"})
    try:
        started("scrape")
        stage = report(fake, "scrape")
        run_pid = next(pid for pid in children(serve_proc.pid)
                       if b"fightcard.service\0run" in Path(f"/proc/{pid}/cmdline").read_bytes())
        os.kill(run_pid, signal.SIGKILL)
        # scrape, orphaned, is now serve's child; it exits a second later and serve must reap it.
        assert wait_until(lambda: stage["pid"] in children(serve_proc.pid) or proc_state(stage["pid"]) is None)
        time.sleep(2.5)
        zombies = {pid for pid, state in children(serve_proc.pid).items() if state == "Z"}
        assert not zombies, f"zombies left under PID 1: {zombies}"
    finally:
        serve_proc.terminate()
        serve_proc.wait(timeout=15)
