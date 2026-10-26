"""`fightcard-service serve`, the container's PID 1: nginx in the foreground, and a run at every schedule slot.

Runs are child processes (`python -m fightcard.service run`), never two at once. If nginx exits, serve exits
non-zero and Docker restarts the container. SIGTERM/SIGINT end a running run and stop nginx gracefully.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from fightcard.service.runner import Config
from fightcard.service.schedule import next_fire

NGINX = ["nginx", "-g", "daemon off;"]
STOP_TIMEOUT = 20  # seconds; Docker's default stop grace period is 10s, compose's too


def _log(out: Callable[[str], None], **fields: object) -> None:
    out(" ".join(["fightcard-serve", *(f'{k}="{v}"' if " " in str(v) else f"{k}={v}" for k, v in fields.items())]))


def _stop(proc, sig: int) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.send_signal(sig)
    try:
        proc.wait(timeout=STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def serve(cfg: Config, *, spawn: Callable = subprocess.Popen, now: Callable[[], datetime] = lambda: datetime.now(UTC),
          sleep: Callable[[float], None] = time.sleep, out: Callable[[str], None] = lambda line: print(line, flush=True),
          stop: threading.Event | None = None) -> int:
    stop = stop or threading.Event()
    run_cmd = [cfg.python, "-m", "fightcard.service", "run"]
    nginx = spawn(NGINX)
    run = None
    if not (cfg.root / "current").exists():
        _log(out, event="startup", status="started", reason="nothing published yet")
        run = spawn([*run_cmd, "--event", "startup"])
    fire, cron = next_fire(now())
    _log(out, status="ready", next=fire.isoformat(), cron=cron)
    while not stop.is_set():
        if (code := nginx.poll()) is not None:
            _log(out, status="failed", reason=f"nginx exited {code}")
            _stop(run, signal.SIGTERM)
            return code or 1
        if run is not None and run.poll() is not None:
            run = None
        if now() >= fire:
            if run is None:
                _log(out, event="schedule", cron=cron, status="started")
                run = spawn([*run_cmd, "--event", "schedule", "--cron", cron])
            else:
                _log(out, event="schedule", cron=cron, status="busy", reason="previous run still going")
            fire, cron = next_fire(now())
        sleep(1)
    _log(out, status="stopping")
    _stop(run, signal.SIGTERM)
    _stop(nginx, signal.SIGQUIT)  # nginx's graceful shutdown
    return 0


def main() -> int:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    return serve(Config.from_env(os.environ), stop=stop)
