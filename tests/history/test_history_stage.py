"""`fightcard history ingest --html-dir <dir> --db <path> [--dump <dir>]`."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing

from history_helpers import FIXTURES

from fightcard.cli import main
from fightcard.history.store import dump

HTML = FIXTURES / "html"
FLAGGED_31CE = "2eb9b36c09c00d69"  # a win on event 31ceaf0e670c1578's card


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


def strip_flag(page, fight_id: str) -> None:
    """Remove one bout's W/L flag text: the method stays, so the parser gives a result with outcome None."""
    html = page.read_text(encoding="utf-8")
    marker = f'fight-details/{fight_id}"><i class="b-flag__inner"><i class="b-flag__text">win'
    assert html.count(marker) == 1
    page.write_text(html.replace(marker, marker.removesuffix("win")), encoding="utf-8")


def test_a_completed_bout_with_a_method_but_no_outcome_fails_the_whole_ingest(tmp_path, capsys):
    """It would drop out of the rating silently (`rating_bouts` keeps outcome IS NOT NULL)."""
    db, good, bad = tmp_path / "h.sqlite", tmp_path / "good", tmp_path / "bad"
    assert main(["history", "ingest", "--html-dir", str(HTML), "--db", str(db), "--dump", str(good)]) == 0
    html = tmp_path / "html"
    shutil.copytree(HTML, html)
    strip_flag(html / "event-details" / "04076783ca83d6ae.html", "279b4190d9ef1379")
    strip_flag(html / "event-details" / "31ceaf0e670c1578.html", FLAGGED_31CE)
    capsys.readouterr()
    assert main(["history", "ingest", "--html-dir", str(html), "--db", str(db), "--dump", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert (f"history: completed bouts with a method but no W/L outcome: 279b4190d9ef1379, {FLAGGED_31CE}; "
            "the store is unchanged") in err.splitlines()
    assert not bad.exists()
    with closing(sqlite3.connect(db)) as conn:
        dump(conn, bad)
    assert {p.name: p.read_bytes() for p in good.iterdir()} == {p.name: p.read_bytes() for p in bad.iterdir()}
