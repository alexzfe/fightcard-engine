"""`fightcard-service run|serve|health` (also `python -m fightcard.service`). Settings come from the environment:
FIGHTCARD_ROOT, FIGHTCARD_FETCH, FIGHTCARD_HOST, FIGHTCARD_FETCH_HOST, FIGHTCARD_MODEL_DIR, FIGHTCARD_GATE_SCRIPT."""

from __future__ import annotations

import argparse
import os
import signal
import sys

from fightcard.service import health, runner, serve
from fightcard.service.runner import Config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fightcard-service", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="one refresh: gate, stages, publish")
    run.add_argument("--event", default="manual", help="schedule runs ask scripts/should_run.py; others always run")
    run.add_argument("--cron", default="", help="the schedule slot that fired")
    sub.add_parser("serve", help="the container's PID 1: nginx and the scheduler")
    health_cmd = sub.add_parser("health", help="the container's health check: exit 1 if nginx or a run is unhealthy")
    health_cmd.add_argument("--staleness", action="store_true",
                            help=f"instead, exit 1 if what's served is over {health.STALE_AFTER} old (for alerting)")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve.main()
    if args.command == "health":
        return health.main(Config.from_env(os.environ), stale=args.staleness)
    signal.signal(signal.SIGTERM, runner.terminate)
    return runner.run_once(Config.from_env(os.environ), event=args.event, cron=args.cron)


if __name__ == "__main__":
    sys.exit(main())
