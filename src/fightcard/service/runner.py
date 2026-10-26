"""`fightcard-service run`: one refresh, gate → scrape → segments → images → score → publish.

Each stage is a `fightcard` process over <root>/state/data. The run stops at the first stage that exits non-zero;
stages write nothing on failure and only publish switches what nginx serves, so the last good generation stays
live. A flock on <root>/state/run.lock keeps two runs from overlapping. Every step logs one line to stdout.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = "/srv/fightcard"  # the volume, and the publish root deploy/nginx/fightcard-engine.conf serves
LOCKED = 75  # EX_TEMPFAIL: another run holds the lock
_OFF = {"off", "0", "false", "no"}


@dataclass(frozen=True)
class Config:
    root: Path
    fetch: bool  # False: skip the stages that fetch from the network (staging, the container test)
    fightcard: str
    model_dir: Path
    gate_script: Path
    python: str

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
    def history_db(self) -> Path:
        # scrape upserts it, score reads it; both default to <--data>/../history/history.sqlite
        return self.state / "history" / "history.sqlite"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        fetch = env.get("FIGHTCARD_FETCH", "on").lower() not in _OFF
        if fetch_host := env.get("FIGHTCARD_FETCH_HOST"):
            fetch = fetch and env.get("FIGHTCARD_HOST") == fetch_host
        repo = Path(__file__).resolve().parents[3]
        return cls(
            root=Path(env.get("FIGHTCARD_ROOT", ROOT)),
            fetch=fetch,
            fightcard=env.get("FIGHTCARD_BIN", str(Path(sys.executable).with_name("fightcard"))),
            model_dir=Path(env.get("FIGHTCARD_MODEL_DIR", "model")),
            gate_script=Path(env.get("FIGHTCARD_GATE_SCRIPT", repo / "scripts" / "should_run.py")),
            python=sys.executable,
        )


@dataclass(frozen=True)
class Step:
    name: str
    args: tuple[str, ...]
    fetches: bool = False


def steps(cfg: Config) -> list[Step]:
    return [
        Step("scrape", ("scrape", "--cookie-file", str(cfg.cache / "fightcard" / "ufcstats_cookie.json")),
             fetches=True),
        Step("segments", ("segments",), fetches=True),
        Step("images", ("images",), fetches=True),
        Step("score", ("score", "--model-dir", str(cfg.model_dir), "--history-db", str(cfg.history_db))),
        Step("publish", ("publish", "--out", str(cfg.root))),
    ]


def execute(argv: list[str]) -> int:
    return subprocess.run(argv).returncode


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
             execute: Callable[[list[str]], int] = execute,
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
            if step.fetches and not cfg.fetch:
                log(step=step.name, status="skipped", reason="fetch-off")
                continue
            t0 = clock()
            try:
                code = execute([cfg.fightcard, "--data", str(cfg.data), *step.args])
            except OSError as e:
                log(step=step.name, status="failed", exit=127, duration_s=f"{clock() - t0:.2f}", reason=str(e))
                return end("failed", 127, failed_step=step.name, exit=127)
            log(step=step.name, status="ok" if code == 0 else "failed", exit=code,
                duration_s=f"{clock() - t0:.2f}")
            if code != 0:
                return end("failed", code, failed_step=step.name, exit=code)
    return end("ok", 0)
