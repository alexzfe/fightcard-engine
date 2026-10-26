"""`fightcard history backup --db <path>`: a consistent copy of the store, as a .tgz on stdout.

infra's backup flow (`backup: [...]` in sites.yml → backup-app → the homeserver's pull-backup) reads a gzipped tar
from stdout and checks it with `tar -tzf`. The archive holds `history/history.sqlite`, the same layout as the seed
tarball docs/SERVICE.md streams into state/."""

from __future__ import annotations

import io
import sqlite3
import sys
import tarfile
from contextlib import closing
from pathlib import Path

from history_helpers import FIXTURES

from fightcard.cli import main
from fightcard.history.ingest import ingest
from fightcard.history.store import connect
from fightcard.service.runner import Config

MEMBER = "history/history.sqlite"


def store(tmp_path: Path) -> Path:
    db = tmp_path / "state" / "history" / "history.sqlite"
    with closing(connect(db)) as conn:
        ingest(conn, FIXTURES / "html")
    return db


def backup(db: Path, capsysbinary) -> tuple[int, bytes]:
    code = main(["history", "backup", "--db", str(db)])
    return code, capsysbinary.readouterr().out


def restore(archive: bytes, into: Path) -> Path:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        assert tar.getnames() == [MEMBER]
        tar.extractall(into, filter="data")
    return into / MEMBER


def count(db: Path, table: str = "events") -> int:
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_writes_the_store_as_a_tgz_on_stdout(tmp_path, capsysbinary):
    db = store(tmp_path)
    code, archive = backup(db, capsysbinary)
    assert code == 0
    copy = restore(archive, tmp_path / "restored")
    assert count(copy) == count(db) == 7
    assert count(copy, "fights") == count(db, "fights")


def test_the_copy_is_consistent_while_a_writer_is_mid_transaction(tmp_path, capsysbinary):
    db = store(tmp_path)
    writer = sqlite3.connect(db, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("DELETE FROM fights")  # not committed
    try:
        code, archive = backup(db, capsysbinary)
    finally:
        writer.execute("ROLLBACK")
        writer.close()
    assert code == 0
    copy = restore(archive, tmp_path / "restored")
    assert count(copy, "fights") == count(db, "fights") > 0


def test_a_missing_store_fails_without_creating_it(tmp_path, capsysbinary):
    db = tmp_path / "nope" / "history.sqlite"
    code, archive = backup(db, capsysbinary)
    assert code == 1 and archive == b""
    assert not db.parent.exists()


def test_the_archive_restores_where_the_service_keeps_the_store(tmp_path, capsysbinary):
    """Extracting it into state/, as the seeding step does, puts the store where scrape and score look."""
    cfg = Config(root=tmp_path / "srv", fetch=False, fightcard="fightcard", model_dir=Path("m"),
                 gate_script=Path("g"), python=sys.executable)
    _, archive = backup(store(tmp_path), capsysbinary)
    assert restore(archive, cfg.state) == cfg.history_db
