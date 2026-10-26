"""`fightcard history ingest --html-dir <dir> --db <path> [--dump <dir>]`: load a ufcstats HTML cache (layout in
`fightcard.history.ingest`) into the history store. Non-zero exit = the store is unchanged; don't publish."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fightcard.history.ingest import LISTING, ingest
from fightcard.history.store import connect, dump


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("ingest", help="load a ufcstats HTML cache into the history store")
    p.add_argument("--html-dir", type=Path, required=True,
                   help="ufcstats cache: listings/, event-details/, fighter-details/, _fetch_log.jsonl")
    p.add_argument("--db", type=Path, required=True, help="SQLite history store (created if missing)")
    p.add_argument("--dump", type=Path, default=None, help="also write <table>.jsonl, sorted, into this folder")


def run(args: argparse.Namespace) -> int:
    if not (args.html_dir / LISTING).is_file():
        print(f"history: {args.html_dir / LISTING} is missing; it says which events are UFC events",
              file=sys.stderr)
        return 1
    conn = connect(args.db)
    try:
        counts = ingest(conn, args.html_dir)
        if args.dump is not None:
            dump(conn, args.dump)
    finally:
        conn.close()
    print(f"history: {counts['events']} events, {counts['fights']} fights, {counts['fighters']} fighters, "
          f"{counts['pages']} pages -> {args.db}", file=sys.stderr)
    return 0
