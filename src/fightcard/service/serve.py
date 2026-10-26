"""`fightcard-service serve`, the container's PID 1: nginx in the foreground, and a run at every schedule slot.

Runs are child processes (`python -m fightcard.service run`), each leading its own process group, never two at once,
and none longer than RUN_TIMEOUT. A slot that fires during a run waits for it (one slot at most; a daily one, which
the gate always lets through, wins over an hourly one) and starts as soon as it ends. If nginx exits, serve exits
non-zero and Docker restarts the container. SIGTERM/SIGINT stop nginx gracefully and end a running run, together,
within STOP_GRACE.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from fightcard.service import runner
from fightcard.service.runner import Config, logfmt
from fightcard.service.schedule import DAILY_CRON, next_fire

NGINX = ["nginx", "-g", "daemon off;"]
# Docker (and compose) SIGKILL a container 10 s after SIGTERM. serve signals nginx and the run at once and gives
# them STOP_GRACE together, which covers the run's own KILL_GRACE for its stage; then whatever is left gets SIGKILL.
STOP_GRACE = 5
# A backstop above the run's own per-stage limits (a hung gate, a wedged run): every stage at its limit, plus a
# KILL_GRACE each, plus slack. Under the health check's 2 h busy limit.
RUN_TIMEOUT = sum(runner.TIMEOUTS.values()) + len(runner.TIMEOUTS) * runner.KILL_GRACE + 10 * 60


def _log(out: Callable[[str], None], **fields: object) -> None:
    out(logfmt("fightcard-serve", **fields))


def _send(proc, sig: int, group: bool) -> None:
    try:
        if group:
            os.killpg(proc.pid, sig)
        else:
            proc.send_signal(sig)
    except ProcessLookupError:
        pass


def _stop(*targets: tuple[object, int, bool]) -> None:
    """Send each (proc, signal, whole process group?) its signal at once, wait for them together for STOP_GRACE,
    then SIGKILL whatever is left."""
    live = [(proc, sig, group) for proc, sig, group in targets if proc is not None and proc.poll() is None]
    for proc, sig, group in live:
        _send(proc, sig, group)
    deadline = time.monotonic() + STOP_GRACE
    for proc, _, group in live:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _send(proc, signal.SIGKILL, group)
            proc.wait()


def reap(own: list) -> None:
    """Reap every exited orphan. serve is PID 1 and compose gives it no init, so it inherits whatever a killed run
    or stage leaves behind. Its own children (nginx, the run) are left to their Popen, which keeps their exit codes."""
    while True:
        try:
            info = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return
        if info is None:
            return
        mine = next((p for p in own if p is not None and p.pid == info.si_pid), None)
        if mine is not None:
            mine.poll()
        else:
            os.waitpid(info.si_pid, 0)


def serve(cfg: Config, *, spawn: Callable = subprocess.Popen, now: Callable[[], datetime] = lambda: datetime.now(UTC),
          sleep: Callable[[float], None] = time.sleep, out: Callable[[str], None] = lambda line: print(line, flush=True),
          stop: threading.Event | None = None) -> int:
    stop = stop or threading.Event()
    run_cmd = [cfg.python, "-m", "fightcard.service", "run"]
    nginx = spawn(NGINX)
    run = run_started = pending = None

    def start(*args: str):
        nonlocal run_started
        run_started = now()
        return spawn([*run_cmd, *args], start_new_session=True)

    if not (cfg.root / "current").exists():
        _log(out, event="startup", status="started", reason="nothing published yet")
        run = start("--event", "startup")
    fire, cron = next_fire(now(), cfg.schedule_minute)
    _log(out, status="ready", next=fire.isoformat(), cron=cron)
    while not stop.is_set():
        reap([nginx, run])
        if (code := nginx.poll()) is not None:
            _log(out, status="failed", reason=f"nginx exited {code}")
            _stop((run, signal.SIGTERM, True))
            return code or 1
        if run is not None and run.poll() is None and (now() - run_started).total_seconds() > RUN_TIMEOUT:
            _log(out, status="timeout", timeout_s=f"{RUN_TIMEOUT:g}", reason="run took too long; stopping it")
            _stop((run, signal.SIGTERM, True))
        if run is not None and run.poll() is not None:
            run = None
        if run is None and pending is not None:
            _log(out, event="schedule", cron=pending, status="started", reason="queued while the last run went")
            run, pending = start("--event", "schedule", "--cron", pending), None
        if now() >= fire:
            if run is None:
                _log(out, event="schedule", cron=cron, status="started")
                run = start("--event", "schedule", "--cron", cron)
            elif pending == DAILY_CRON:
                _log(out, event="schedule", cron=cron, status="dropped", reason="a daily run is already queued")
            else:
                _log(out, event="schedule", cron=cron, status="queued", reason="previous run still going")
                pending = cron
            fire, cron = next_fire(now(), cfg.schedule_minute)
        sleep(1)
    _log(out, status="stopping")
    _stop((nginx, signal.SIGQUIT, False), (run, signal.SIGTERM, True))  # SIGQUIT: nginx's graceful shutdown
    reap([])
    return 0


def main() -> int:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    return serve(Config.from_env(os.environ), stop=stop)
