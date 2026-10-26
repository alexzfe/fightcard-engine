"""`fightcard-service run`: one refresh, gate → scrape → segments → images → model pull → score → publish.

Each stage is a `fightcard` process over <root>/state/data, in its own session (so its process group holds it and
anything it starts) at nice NICE. The run stops at the first stage that exits non-zero or outlives its TIMEOUTS
entry, except the model pull, which is logged and skipped past: it keeps the current model when it can't adopt a
new one, and score then runs on that. Stages write nothing on failure and only publish switches what nginx serves,
so the last good generation stays live. A flock on <root>/state/run.lock keeps two runs from overlapping; each stage inherits the locked fd,
so the lock is held until the stage is gone too, even if the run itself is killed. SIGTERM (see `terminate`) ends
the stage's process group first, then the run. Every step logs one line to stdout.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fightcard.service.schedule import MINUTE

ROOT = "/srv/fightcard"  # the volume, and the publish root deploy/nginx/fightcard-engine.conf serves
LOCKED = 75  # EX_TEMPFAIL: another run holds the lock
_OFF = {"off", "0", "false", "no"}

# Each stage's wall-clock limit, in seconds: well past its normal duration (scrape takes minutes, score seconds),
# so only a hang hits it. Then the stage's process group gets SIGTERM, KILL_GRACE seconds, SIGKILL, and the run
# fails with TIMED_OUT (timeout(1)'s code).
TIMEOUTS = {"scrape": 30 * 60, "segments": 15 * 60, "images": 15 * 60, "model": 5 * 60, "score": 10 * 60,
            "publish": 10 * 60}
KILL_GRACE = 3
TIMED_OUT = 124
TERMINATED = 128 + signal.SIGTERM
NICE = 10  # stages yield the box's 2 vCPUs to nginx and to the other env


@dataclass(frozen=True)
class Config:
    root: Path
    fetch: bool  # False: skip the stages that fetch from the network (staging, the container test)
    fightcard: str
    model_dir: Path
    gate_script: Path
    python: str
    schedule_minute: int = MINUTE  # the hourly slot's; off the fetch host (staging) half an hour later
    model_url: str | None = None  # FIGHTCARD_MODEL_URL: where `fightcard model pull` looks; None: no pulls
    model_pin: str | None = None  # FIGHTCARD_MODEL_PIN: a kept model version to run instead

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def data(self) -> Path:
        return self.state / "data"

    @property
    def cache(self) -> Path:
        return self.state / "cache"

    @property
    def lock(self) -> Path:
        return self.state / "run.lock"

    @property
    def run_marker(self) -> Path:
        # {"pid", "started"} while a run holds the lock; `fightcard-service health` reads it
        return self.state / "run.json"

    @property
    def history_db(self) -> Path:
        # scrape upserts it, score reads it; both default to <--data>/../history/history.sqlite
        return self.state / "history" / "history.sqlite"

    @property
    def model_state(self) -> Path:
        # `fightcard model pull` keeps its versions here, and `current` -> the one score reads
        return self.state / "model"

    @property
    def scoring_model(self) -> Path:
        """What score reads: the pulled model once there is one, else the image's baked model."""
        current = self.model_state / "current"
        return current if current.is_dir() else self.model_dir

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        fetch = env.get("FIGHTCARD_FETCH", "on").lower() not in _OFF
        if fetch_host := env.get("FIGHTCARD_FETCH_HOST"):
            fetch = fetch and env.get("FIGHTCARD_HOST") == fetch_host
        repo = Path(__file__).resolve().parents[3]
        minute = env.get("FIGHTCARD_SCHEDULE_MINUTE")
        if minute is None:
            # Off the fetch host (staging), half an hour after prod, so the two envs never score at once.
            minute = str(MINUTE if env.get("FIGHTCARD_HOST", fetch_host) == fetch_host else (MINUTE + 30) % 60)
        if not (minute.isdigit() and int(minute) < 60):
            raise ValueError(f"FIGHTCARD_SCHEDULE_MINUTE must be 0-59, not {minute!r}")
        return cls(
            root=Path(env.get("FIGHTCARD_ROOT", ROOT)),
            fetch=fetch,
            fightcard=env.get("FIGHTCARD_BIN", str(Path(sys.executable).with_name("fightcard"))),
            model_dir=Path(env.get("FIGHTCARD_MODEL_DIR", "model")),
            gate_script=Path(env.get("FIGHTCARD_GATE_SCRIPT", repo / "scripts" / "should_run.py")),
            python=sys.executable,
            schedule_minute=int(minute),
            model_url=env.get("FIGHTCARD_MODEL_URL") or None,
            model_pin=env.get("FIGHTCARD_MODEL_PIN") or None,
        )


@dataclass(frozen=True)
class Step:
    name: str
    args: tuple[str, ...] | Callable[[], tuple[str, ...]]  # a callable is asked when the step starts
    fetches: bool = False
    fatal: bool = True  # False: a failure is logged and the run goes on


def steps(cfg: Config) -> list[Step]:
    return [
        Step("scrape", ("scrape", "--cookie-file", str(cfg.cache / "fightcard" / "ufcstats_cookie.json")),
             fetches=True),
        Step("segments", ("segments",), fetches=True),
        Step("images", ("images",), fetches=True),
        Step("model", ("model", "pull", "--root", str(cfg.model_state), "--seed-dir", str(cfg.model_dir),
                       *(("--url", cfg.model_url) if cfg.model_url else ()),
                       *(("--pin", cfg.model_pin) if cfg.model_pin else ())), fatal=False),
        # The pull may have just made state/model/current, so which model is only known when score starts.
        Step("score", lambda: ("score", "--model-dir", str(cfg.scoring_model), "--history-db", str(cfg.history_db))),
        Step("publish", ("publish", "--out", str(cfg.root))),
    ]


class StageTimeout(Exception):
    pass


class Terminated(BaseException):
    """Raised by `terminate`, so a SIGTERM unwinds through `execute`, which ends the stage's group on its way out."""


def terminate(signum: int, frame: object) -> None:
    """The SIGTERM handler `fightcard-service run` installs. A second TERM is ignored, so it can't interrupt the
    cleanup the first one started."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise Terminated


def end_group(proc: subprocess.Popen) -> None:
    """SIGTERM the process group `proc` leads, SIGKILL it KILL_GRACE seconds later (whatever ignored the TERM, or
    a grandchild the stage left behind), and reap `proc`."""
    for sig, wait in ((signal.SIGTERM, KILL_GRACE), (signal.SIGKILL, None)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            pass


def execute(argv: list[str], *, timeout: float, pass_fds: tuple[int, ...] = ()) -> int:
    # The run process has no threads, so preexec_fn is safe here.
    proc = subprocess.Popen(argv, start_new_session=True, pass_fds=pass_fds, preexec_fn=lambda: os.nice(NICE))
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        end_group(proc)
        raise StageTimeout from None
    except BaseException:  # Terminated, KeyboardInterrupt: the stage goes first
        end_group(proc)
        raise


def script_gate(cfg: Config, event: str, cron: str) -> tuple[bool, str]:
    """Ask scripts/should_run.py, unchanged: it prints its reason and writes run=true|false to $GITHUB_OUTPUT."""
    with tempfile.NamedTemporaryFile("r", suffix=".out") as output:
        result = subprocess.run(
            [cfg.python, str(cfg.gate_script), "--data", str(cfg.data), "--event-name", event, "--schedule", cron],
            capture_output=True, text=True, env={**os.environ, "GITHUB_OUTPUT": output.name},
        )
        if result.returncode != 0:
            detail = (result.stderr.strip().splitlines() or ["no output"])[-1]
            raise RuntimeError(f"{cfg.gate_script.name} exited {result.returncode}: {detail}")
        decision = dict(line.split("=", 1) for line in output.read().splitlines() if "=" in line)
    reason = (result.stdout.splitlines() or [""])[0]
    return decision.get("run") == "true", reason


def logfmt(prefix: str, **fields: object) -> str:
    """`prefix k=v k2="v w"`: one greppable line; values with spaces (or empty) are quoted."""
    def fmt(value: object) -> str:
        text = str(value)
        return f'"{text}"' if not text or any(c.isspace() for c in text) else text
    return " ".join([prefix, *(f"{k}={fmt(v)}" for k, v in fields.items())])


def run_once(cfg: Config, *, event: str = "manual", cron: str = "",
             execute: Callable[..., int] = execute,
             gate: Callable[[Config, str, str], tuple[bool, str]] = script_gate,
             out: Callable[[str], None] = lambda line: print(line, flush=True),
             clock: Callable[[], float] = time.monotonic, run_id: str | None = None) -> int:
    run_id = run_id or f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{os.getpid()}"

    def log(**fields: object) -> None:
        out(logfmt(f"fightcard-run run={run_id}", **fields))

    started = clock()

    def end(status: str, code: int, **fields: object) -> int:
        log(status=status, **fields, duration_s=f"{clock() - started:.2f}")
        return code

    log(status="start", event=event, cron=cron, fetch="on" if cfg.fetch else "off")
    cfg.data.mkdir(parents=True, exist_ok=True)
    cfg.cache.mkdir(parents=True, exist_ok=True)
    cfg.history_db.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.lock, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return end("locked", LOCKED)

        step_name = "gate"
        cfg.run_marker.write_text(json.dumps({"pid": os.getpid(), "started": datetime.now(UTC).isoformat()}))
        try:
            if event == "schedule":
                try:
                    go, reason = gate(cfg, event, cron)
                except Exception as e:  # noqa: BLE001 - logged, and the run fails
                    log(step="gate", status="failed", reason=str(e))
                    return end("failed", 1, failed_step="gate", exit=1)
                log(step="gate", status="run" if go else "skip", reason=reason)
                if not go:
                    return end("skipped", 0)

            for step in steps(cfg):
                step_name = step.name
                if step.fetches and not cfg.fetch:
                    log(step=step.name, status="skipped", reason="fetch-off")
                    continue
                t0 = clock()
                limit = TIMEOUTS[step.name]
                args = step.args() if callable(step.args) else step.args
                try:
                    code = execute([cfg.fightcard, "--data", str(cfg.data), *args], timeout=limit,
                                   pass_fds=(lock.fileno(),))
                except StageTimeout:
                    log(step=step.name, status="timeout", timeout_s=f"{limit:g}", duration_s=f"{clock() - t0:.2f}")
                    if not step.fatal:
                        continue
                    return end("failed", TIMED_OUT, failed_step=step.name, exit=TIMED_OUT)
                except OSError as e:
                    log(step=step.name, status="failed", exit=127, duration_s=f"{clock() - t0:.2f}", reason=str(e))
                    if not step.fatal:
                        continue
                    return end("failed", 127, failed_step=step.name, exit=127)
                log(step=step.name, status="ok" if code == 0 else "failed", exit=code,
                    duration_s=f"{clock() - t0:.2f}")
                if code != 0 and step.fatal:
                    return end("failed", code, failed_step=step.name, exit=code)
        except Terminated:
            return end("terminated", TERMINATED, failed_step=step_name)
        finally:
            cfg.run_marker.unlink(missing_ok=True)
    return end("ok", 0)
