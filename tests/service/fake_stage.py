"""A stand-in for the `fightcard` binary in the supervision tests: a real process that behaves as told.

Called as `fightcard --data <dir> <stage> …`. FAKE_DIR is where it writes `<stage>.json` (its pid, process group,
niceness, and whether state/run.lock was free). Per stage, upper-cased: FAKE_SLEEP_<STAGE> seconds to sleep,
FAKE_EXIT_<STAGE> its exit code, FAKE_IGNORE_TERM_<STAGE>=1 to ignore SIGTERM, FAKE_CHILD_<STAGE>=1 to start a
`sleep` grandchild in its process group first (its pid goes in the json as `child`)."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

STAGES = ("scrape", "segments", "images", "score", "publish")


def main() -> int:
    stage = next(a for a in sys.argv[1:] if a in STAGES)
    data = Path(sys.argv[sys.argv.index("--data") + 1])

    def env(key: str, default: str = "") -> str:
        return os.environ.get(f"FAKE_{key}_{stage.upper()}", default)

    if env("IGNORE_TERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    report = {"pid": os.getpid(), "pgid": os.getpgid(0), "nice": os.nice(0)}
    lock = data.parent / "run.lock"
    if lock.exists():
        with open(lock, "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                report["lock"] = "free"
            except BlockingIOError:
                report["lock"] = "held"
    if env("CHILD"):
        report["child"] = subprocess.Popen(["sleep", "300"]).pid
    (Path(os.environ["FAKE_DIR"]) / f"{stage}.json").write_text(json.dumps(report))
    time.sleep(float(env("SLEEP", "0")))
    return int(env("EXIT", "0"))


if __name__ == "__main__":
    sys.exit(main())
