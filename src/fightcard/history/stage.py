"""`fightcard history ingest --html-dir <dir> --db <path> [--dump <dir>]`: load a ufcstats HTML cache (layout in
`fightcard.history.ingest`) into the history store. Non-zero exit = the store is unchanged; don't publish. It exits 1
when a completed bout has a method but no W/L outcome, naming the fights.

`fightcard history backup --db <path>`: a consistent copy of the store (SQLite's online backup, so a scrape writing
meanwhile is fine) to stdout as a .tgz holding `history/history.sqlite`, the layout the seed tarball uses. infra's
backup flow runs it nightly in the prod container (docs/SERVICE.md).

`fightcard history rankings --db <path> [--as-of YYYY-MM-DD] [--report]`: the rankings modules
(`fightcard.history.rankings`) over the whole store, as JSON on stdout; with --report, one line per module with its
rows and compact JSON size, raw and gzipped, to see what the site can carry. Read-only."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tarfile
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import BinaryIO

from fightcard.history.ingest import LISTING, MissingOutcome, ingest
from fightcard.history.rankings import active_index, all_time_peaks, division_ranks, divisions, p4p, size_report
from fightcard.history.rating import rate
from fightcard.history.store import connect, dump, names, rating_bouts


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("ingest", help="load a ufcstats HTML cache into the history store")
    p.add_argument("--html-dir", type=Path, required=True,
                   help="ufcstats cache: listings/, event-details/, fighter-details/, _fetch_log.jsonl")
    p.add_argument("--db", type=Path, required=True, help="SQLite history store (created if missing)")
    p.add_argument("--dump", type=Path, default=None, help="also write <table>.jsonl, sorted, into this folder")
    b = sub.add_parser("backup", help="write a consistent copy of the store to stdout, as a .tgz")
    b.add_argument("--db", type=Path, required=True, help="SQLite history store")
    r = sub.add_parser("rankings", help="P4P, active index, division ranks and all-time peaks from the store")
    r.add_argument("--db", type=Path, required=True, help="SQLite history store")
    r.add_argument("--as-of", type=date.fromisoformat, default=None, help="rate up to this day (default: today)")
    r.add_argument("--report", action="store_true", help="print each module's rows and JSON size instead")


def backup(db: Path, out: BinaryIO) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "history.sqlite"
        # Read-write, so a hot journal a killed scrape left behind is rolled back rather than refused.
        with closing(sqlite3.connect(db)) as src, closing(sqlite3.connect(copy)) as dst:
            src.backup(dst)
        with tarfile.open(fileobj=out, mode="w|gz") as tar:
            tar.add(copy, arcname="history/history.sqlite")
    out.flush()


def rankings(conn: sqlite3.Connection, now: date) -> dict[str, object]:
    """Every rankings module over the store's bouts up to `now`."""
    bouts = rating_bouts(conn, now.isoformat())
    who = names(conn)
    ratings = rate(bouts, who)[1].set_index("fighter_id")
    divs = divisions(bouts)
    return {"p4p": p4p(ratings, who, divs, now), "active_index": active_index(ratings, who, divs, now),
            "division_ranks": division_ranks(ratings, who, divs, now),
            "all_time_peaks": all_time_peaks(ratings, who, now)}


def run_rankings(db: Path, now: date, report: bool) -> int:
    if not db.is_file():
        print(f"history: {db} is missing; run `fightcard history ingest` first", file=sys.stderr)
        return 1
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        modules = rankings(conn, now)
    if not report:
        print(json.dumps(modules, ensure_ascii=False, indent=1))
        return 0
    print(f"{'module':<16}{'rows':>7}{'raw KB':>10}{'gzip KB':>10}")
    for line in size_report(modules):
        print(f"{line['module']:<16}{line['rows']:>7}{line['raw_bytes'] / 1024:>10.1f}{line['gzip_bytes'] / 1024:>10.1f}")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.action == "rankings":
        return run_rankings(args.db, args.as_of or date.today(), args.report)
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
