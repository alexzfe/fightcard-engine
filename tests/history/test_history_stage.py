"""`fightcard history ingest --html-dir <dir> --db <path> [--dump <dir>]`."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from history_helpers import FIXTURES

from fightcard.cli import main

HTML = FIXTURES / "html"


def test_ingest_writes_the_store_and_reports_counts(tmp_path, capsys):
    db = tmp_path / "h" / "history.sqlite"
    assert main(["history", "ingest", "--html-dir", str(HTML), "--db", str(db)]) == 0
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 7
    err = capsys.readouterr().err
    assert "7 events" in err and "10 pages" in err


def test_ingest_can_write_the_sorted_dump(tmp_path):
    out = tmp_path / "dump"
    assert main(["history", "ingest", "--html-dir", str(HTML), "--db", str(tmp_path / "h.sqlite"),
                 "--dump", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["events.jsonl", "fighters.jsonl", "fights.jsonl", "pages.jsonl"]


def test_missing_html_dir_or_listing_fails_without_touching_the_store(tmp_path, capsys):
    db = tmp_path / "h.sqlite"
    assert main(["history", "ingest", "--html-dir", str(tmp_path / "nope"), "--db", str(db)]) == 1
    (tmp_path / "html" / "event-details").mkdir(parents=True)
    assert main(["history", "ingest", "--html-dir", str(tmp_path / "html"), "--db", str(db)]) == 1
    assert "events_completed" in capsys.readouterr().err
    assert not db.exists()
