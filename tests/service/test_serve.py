"""fightcard-service serve: PID 1 runs nginx and starts a run at each slot, with fake processes and clock."""

from __future__ import annotations

import itertools
import signal
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from fightcard.service import runner, serve
from fightcard.service.runner import Config

REPO = Path(__file__).resolve().parents[2]


class FakeProc:
    pids = itertools.count(2**22 + 1)  # above Linux's pid_max, so never a real process

    def __init__(self, argv, world: World, lifetime: int | None, **kwargs):
        """`lifetime`: how many of the world's ticks it runs for (None: until signalled)."""
        self.argv, self.world, self.code, self.kwargs = argv, world, None, kwargs
        self.ends_at = None if lifetime is None else world.ticks + lifetime
        self.pid = next(self.pids)
        self.signals: list[int] = []

    def poll(self):
        if self.code is None and self.ends_at is not None and self.world.ticks >= self.ends_at:
            self.code = 0
        return self.code

    def send_signal(self, sig):
        self.signals.append(sig)
        self.code = -sig

    def terminate(self):
        self.send_signal(signal.SIGTERM)

    def wait(self, timeout=None):
        return self.code


class World:
    """A clock that advances 30s per sleep, a stop event, and a spawn that hands out FakeProcs."""

    def __init__(self, start: str, steps: int, run_lifetime: int | None = 1, nginx_dies_at: int | None = None):
        self.now = datetime.fromisoformat(start).replace(tzinfo=UTC)
        self.steps, self.run_lifetime, self.nginx_dies_at = steps, run_lifetime, nginx_dies_at
        self.stop = threading.Event()
        self.procs: list[FakeProc] = []
        self.spawned_at: list[datetime] = []
        self.lines: list[str] = []
        self.ticks = 0

    def spawn(self, argv, **kwargs):
        proc = FakeProc(argv, self, None if argv[0] == "nginx" else self.run_lifetime, **kwargs)
        self.procs.append(proc)
        self.spawned_at.append(self.now)
        return proc

    def killpg(self, pgid, sig):
        next(p for p in self.procs if p.pid == pgid).send_signal(sig)

    def sleep(self, seconds):
        self.ticks += 1
        self.now += timedelta(seconds=30)
        if self.nginx_dies_at is not None and self.ticks == self.nginx_dies_at:
            self.nginx.code = 1
        if self.ticks >= self.steps:
            self.stop.set()

    @property
    def nginx(self) -> FakeProc:
        return self.procs[0]

    @property
    def runs(self) -> list[list[str]]:
        return [p.argv for p in self.procs[1:]]

    def serve(self, cfg: Config) -> int:
        with mock.patch.object(serve.os, "killpg", self.killpg):
            return serve.serve(cfg, spawn=self.spawn, now=lambda: self.now, sleep=self.sleep, out=self.lines.append,
                               stop=self.stop)


def config(tmp_path: Path, published: bool = True) -> Config:
    cfg = Config(root=tmp_path / "srv", fetch=False, fightcard="fightcard", model_dir=Path("model"),
                 gate_script=REPO / "scripts" / "should_run.py", python=sys.executable)
    if published:
        (cfg.root / "generations" / "x").mkdir(parents=True)
        (cfg.root / "current").symlink_to("generations/x")
    return cfg


RUN = [sys.executable, "-m", "fightcard.service", "run"]


def test_starts_nginx_in_the_foreground(tmp_path):
    world = World("2026-10-26 05:50:00", steps=2)
    world.serve(config(tmp_path))
    assert world.nginx.argv == ["nginx", "-g", "daemon off;"]


def test_runs_at_each_slot_with_its_cron(tmp_path):
    world = World("2026-10-26 05:50:00", steps=60)  # 30 minutes
    assert world.serve(config(tmp_path)) == 0
    assert world.runs == [RUN + ["--event", "schedule", "--cron", "0 6 * * *"],
                          RUN + ["--event", "schedule", "--cron", "17 * * * *"]]


def test_a_run_on_an_empty_root_starts_at_once(tmp_path):
    world = World("2026-10-26 05:50:00", steps=2)
    world.serve(config(tmp_path, published=False))
    assert world.runs == [RUN + ["--event", "startup"]]


def test_never_starts_a_run_while_the_last_is_still_going(tmp_path):
    world = World("2026-10-26 05:50:00", steps=60, run_lifetime=None)  # the 06:00 run never ends
    world.serve(config(tmp_path))
    assert len(world.runs) == 1
    assert any("status=queued" in line and 'cron="17 * * * *"' in line for line in world.lines)


# Finding 2: a slot that fires during a run waits for it instead of being dropped.

HOURLY = RUN + ["--event", "schedule", "--cron", "17 * * * *"]
DAILY = RUN + ["--event", "schedule", "--cron", "0 6 * * *"]


def test_a_busy_slot_runs_as_soon_as_the_run_ends(tmp_path):
    world = World("2026-10-26 05:59:40", steps=60, run_lifetime=40)  # the 06:00 run ends at about 06:20
    world.serve(config(tmp_path))
    assert world.runs == [DAILY, HOURLY]
    assert world.spawned_at[2] < datetime(2026, 10, 26, 6, 25, tzinfo=UTC)  # not at the next slot, 07:17


STARTUP = RUN + ["--event", "startup"]


@pytest.mark.parametrize("start,published,until,first", [
    ("04:50:00", False, "06:05", STARTUP),  # 05:17 queues, then 06:00 takes its place
    ("05:16:40", True, "06:20", HOURLY),    # 06:00 queues, then 06:17 doesn't take its place
])
def test_a_pending_daily_slot_wins_over_an_hourly_one(tmp_path, start, published, until, first):
    begin = datetime.fromisoformat(f"2026-10-26 {start}").replace(tzinfo=UTC)
    lifetime = int((datetime.fromisoformat(f"2026-10-26 {until}").replace(tzinfo=UTC) - begin).total_seconds() // 30)
    assert lifetime * 30 < serve.RUN_TIMEOUT
    world = World(f"2026-10-26 {start}", steps=lifetime + 20, run_lifetime=lifetime)
    world.serve(config(tmp_path, published=published))
    assert world.runs == [first, DAILY]  # the daily one bypasses the gate, so it's the one worth keeping


def test_at_most_one_slot_waits(tmp_path):
    # A startup run from 06:59 to about 08:20 spans the 07:17 and 08:17 slots; only one run follows it.
    world = World("2026-10-26 06:59:00", steps=180, run_lifetime=162)
    world.serve(config(tmp_path, published=False))
    assert world.runs == [STARTUP, HOURLY]
    assert world.spawned_at[2] < datetime(2026, 10, 26, 8, 25, tzinfo=UTC)


def test_stop_ends_the_run_and_stops_nginx_gracefully(tmp_path):
    world = World("2026-10-26 05:59:40", steps=2, run_lifetime=None)
    assert world.serve(config(tmp_path)) == 0
    assert world.nginx.signals == [signal.SIGQUIT]
    assert world.procs[1].signals == [signal.SIGTERM]


def test_nginx_exiting_ends_serve_with_an_error(tmp_path):
    world = World("2026-10-26 05:59:40", steps=10, run_lifetime=None, nginx_dies_at=3)
    assert world.serve(config(tmp_path)) == 1
    assert world.procs[1].signals == [signal.SIGTERM]
    assert any("nginx exited" in line for line in world.lines)


# Finding 1: a cap on the whole run. Finding 3: a shutdown that fits Docker's 10 s.

def test_runs_lead_their_own_process_group(tmp_path):
    world = World("2026-10-26 05:59:40", steps=3)
    world.serve(config(tmp_path))
    assert world.procs[1].kwargs == {"start_new_session": True}


def test_the_run_cap_covers_every_stage_and_stays_under_the_busy_health_limit():
    assert serve.RUN_TIMEOUT >= sum(runner.TIMEOUTS.values()) + len(runner.TIMEOUTS) * runner.KILL_GRACE
    assert serve.RUN_TIMEOUT < 2 * 3600


def test_a_run_past_the_cap_is_stopped_and_the_schedule_goes_on(tmp_path):
    world = World("2026-10-26 05:59:40", steps=2 * serve.RUN_TIMEOUT // 30 + 10, run_lifetime=None)
    world.serve(config(tmp_path))
    assert world.procs[1].signals[0] == signal.SIGTERM
    assert any("status=timeout" in line and f"timeout_s={serve.RUN_TIMEOUT}" in line for line in world.lines)
    assert len(world.runs) >= 2


def test_the_shutdown_budget_fits_dockers_default_grace():
    # The run needs KILL_GRACE to end its stage's group; Docker sends SIGKILL at 10 s.
    assert runner.KILL_GRACE < serve.STOP_GRACE <= 7


def test_main_installs_signal_handlers_that_stop_serve(monkeypatch):
    seen = {}

    def fake_serve(cfg, **kwargs):
        seen["cfg"] = cfg
        handler = signal.getsignal(signal.SIGTERM)
        handler(signal.SIGTERM, None)
        seen["stopped"] = kwargs["stop"].is_set()
        return 0

    monkeypatch.setattr(serve, "serve", fake_serve)
    previous = signal.getsignal(signal.SIGTERM)
    try:
        assert serve.main() == 0
    finally:
        signal.signal(signal.SIGTERM, previous)
        signal.signal(signal.SIGINT, signal.default_int_handler)
    assert seen["stopped"] is True
