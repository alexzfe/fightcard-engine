"""fightcard-service serve: PID 1 runs nginx and starts a run at each slot, with fake processes and clock."""

from __future__ import annotations

import signal
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fightcard.service import serve
from fightcard.service.runner import Config

REPO = Path(__file__).resolve().parents[2]


class FakeProc:
    def __init__(self, argv, lifetime: int | None):
        self.argv, self.lifetime, self.code = argv, lifetime, None
        self.signals: list[int] = []

    def poll(self):
        if self.code is None and self.lifetime is not None:
            self.lifetime -= 1
            if self.lifetime <= 0:
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
        self.lines: list[str] = []
        self.ticks = 0

    def spawn(self, argv):
        proc = FakeProc(argv, None if argv[0] == "nginx" else self.run_lifetime)
        self.procs.append(proc)
        return proc

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
        return serve.serve(cfg, spawn=self.spawn, now=lambda: self.now, sleep=self.sleep, out=self.lines.append,
                           stop=self.stop)


def config(tmp_path: Path, published: bool = True) -> Config:
    cfg = Config(root=tmp_path / "srv", fetch=False, fightcard="fightcard", model_dir=Path("model"),
                 gate_script=REPO / "scripts" / "should_run.py", python=sys.executable)
    if published:
        cfg.root.mkdir(parents=True)
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
    assert any("status=busy" in line and 'cron="17 * * * *"' in line for line in world.lines)


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
