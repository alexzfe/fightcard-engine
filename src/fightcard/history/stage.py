"""`fightcard history ingest --html-dir <dir> --db <path> [--dump <dir>]`: load a ufcstats HTML cache (layout in
`fightcard.history.ingest`) into the history store. Non-zero exit = the store is unchanged; don't publish. It exits 1
when a completed bout has a method but no W/L outcome, naming the fights.

`fightcard history backup --db <path>`: a consistent copy of the store (SQLite's online backup, so a scrape writing
meanwhile is fine) to stdout as a .tgz holding `history/history.sqlite`, the layout the seed tarball uses. infra's
backup flow runs it nightly in the prod container (docs/SERVICE.md)."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path
from typing import BinaryIO

from fightcard.history.ingest import LISTING, MissingOutcome, ingest
from fightcard.history.store import connect, dump


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("ingest", help="load a ufcstats HTML cache into the history store")
    p.add_argument("--html-dir", type=Path, required=True,
                   help="ufcstats cache: listings/, event-details/, fighter-details/, _fetch_log.jsonl")
    p.add_argument("--db", type=Path, required=True, help="SQLite history store (created if missing)")
    p.add_argument("--dump", type=Path, default=None, help="also write <table>.jsonl, sorted, into this folder")
    b = sub.add_parser("backup", help="write a consistent copy of the store to stdout, as a .tgz")
    b.add_argument("--db", type=Path, required=True, help="SQLite history store")


def backup(db: Path, out: BinaryIO) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "history.sqlite"
        # Read-write, so a hot journal a killed scrape left behind is rolled back rather than refused.
        with closing(sqlite3.connect(db)) as src, closing(sqlite3.connect(copy)) as dst:
            src.backup(dst)
        with tarfile.open(fileobj=out, mode="w|gz") as tar:
            tar.add(copy, arcname="history/history.sqlite")
    out.flush()


def run(args: argparse.Namespace) -> int:
    if args.action == "backup":
        if not args.db.is_file():
            print(f"history: {args.db} is missing; nothing to back up", file=sys.stderr)
            return 1
        backup(args.db, sys.stdout.buffer)
        return 0
    if not (args.html_dir / LISTING).is_file():
        print(f"history: {args.html_dir / LISTING} is missing; it says which events are UFC events",
              file=sys.stderr)
        return 1
    conn = connect(args.db)
    try:
        counts = ingest(conn, args.html_dir)
        if args.dump is not None:
            dump(conn, args.dump)
    except MissingOutcome as e:
        print(f"history: {e}; the store is unchanged", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"history: {counts['events']} events, {counts['fights']} fights, {counts['fighters']} fighters, "
          f"{counts['pages']} pages -> {args.db}", file=sys.stderr)
    return 0
