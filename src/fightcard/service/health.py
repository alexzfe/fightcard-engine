"""`fightcard-service health`, the image's HEALTHCHECK: healthy while nginx answers and no run is stuck.

It ignores the data's age: deploy-app rolls back an unhealthy container, so a check that failed while ufcstats was
down would block every deploy until an external site recovered. `health --staleness` reports the age instead, for
alerting. nginx's own /healthz (infra's public smoke test) stays a plain 200."""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import UTC, datetime, timedelta

from fightcard.service.runner import Config

URL = "http://127.0.0.1:8080/healthz"
STALE_AFTER = timedelta(hours=26)  # --staleness: the daily 06:00 run always scrapes; 2 h of slack for it to finish
BUSY_LIMIT = timedelta(hours=2)  # above serve's RUN_TIMEOUT, so only a run that cap failed to stop trips it


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def _hours(age: timedelta) -> str:
    minutes = int(age.total_seconds()) // 60
    return f"{minutes // 60} h {minutes % 60:02d} min"


def _generated_at(cfg: Config) -> tuple[str, datetime]:
    text = json.loads((cfg.root / "current" / "index.json").read_text())["generated_at"]
    return text, datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def staleness(cfg: Config, *, now: datetime) -> list[str]:
    """What's wrong with the age of what's served, if anything. A volume with nothing published passes."""
    if not (cfg.root / "current").exists():
        return []
    try:
        text, generated = _generated_at(cfg)
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [f"current/index.json: no readable generated_at: {e}"]
    if now - generated > STALE_AFTER:
        return [f"stale: generated_at {text}, age {_hours(now - generated)}, over {_hours(STALE_AFTER)}"]
    return []


def check(cfg: Config, *, now: datetime, url: str | None = None) -> list[str]:
    """What's wrong, if anything."""
    url = url or URL
    try:
        with urllib.request.urlopen(url, timeout=2):
            pass
    except OSError as e:
        return [f"nginx: {url}: {e}"]

    problems = []
    try:
        marker = json.loads(cfg.run_marker.read_text())
        pid, started = int(marker["pid"]), datetime.fromisoformat(marker["started"])
    except (OSError, ValueError, KeyError, TypeError):
        pass  # no run, or one just starting
    else:
        if _alive(pid) and now - started > BUSY_LIMIT:
            problems.append(f"busy: run pid {pid} started {marker['started']}, over {BUSY_LIMIT} ago")
    return problems


def main(cfg: Config, *, stale: bool = False) -> int:
    now = datetime.now(UTC)
    problems = staleness(cfg, now=now) if stale else check(cfg, now=now)
    if stale and not problems and (cfg.root / "current").exists():
        text, generated = _generated_at(cfg)
        print(f"ok: generated_at {text}, age {_hours(now - generated)}")
        return 0
    print("\n".join(problems) or "ok")
    return 1 if problems else 0
