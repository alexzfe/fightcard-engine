"""`fightcard-service health`, the image's HEALTHCHECK: healthy while nginx answers, what it serves is fresh, and no
run is stuck.

nginx's own /healthz (infra's public smoke test) stays a plain 200, and so does this check on a volume with nothing
published yet: the first deploy comes before seeding and must not be rolled back."""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import UTC, datetime, timedelta

from fightcard.service.runner import Config

URL = "http://127.0.0.1:8080/healthz"
STALE_AFTER = timedelta(hours=26)  # the daily 06:00 run always scrapes; 2 h of slack for it to finish
BUSY_LIMIT = timedelta(hours=2)  # above serve's RUN_TIMEOUT, so only a run that cap failed to stop trips it


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def check(cfg: Config, *, now: datetime, url: str | None = None) -> list[str]:
    """What's wrong, if anything."""
    url = url or URL
    try:
        with urllib.request.urlopen(url, timeout=2):
            pass
    except OSError as e:
        return [f"nginx: {url}: {e}"]

    problems = []
    if (cfg.root / "current").exists():
        index = cfg.root / "current" / "index.json"
        try:
            text = json.loads(index.read_text())["generated_at"]
            generated = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except (OSError, ValueError, KeyError, TypeError) as e:
            problems.append(f"current/index.json: no readable generated_at: {e}")
        else:
            # Only where this env scrapes: staging rescores the same data forever.
            if cfg.fetch and now - generated > STALE_AFTER:
                problems.append(f"stale: generated_at {text} is over {STALE_AFTER} old")

    try:
        marker = json.loads(cfg.run_marker.read_text())
        pid, started = int(marker["pid"]), datetime.fromisoformat(marker["started"])
    except (OSError, ValueError, KeyError, TypeError):
        pass  # no run, or one just starting
    else:
        if _alive(pid) and now - started > BUSY_LIMIT:
            problems.append(f"busy: run pid {pid} started {marker['started']}, over {BUSY_LIMIT} ago")
    return problems


def main(cfg: Config) -> int:
    problems = check(cfg, now=datetime.now(UTC))
    print("\n".join(problems) or "ok")
    return 1 if problems else 0
