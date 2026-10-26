from __future__ import annotations

import pytest
from history_helpers import SEED_HTML, needs_seed

from fightcard.history.ingest import ingest
from fightcard.history.store import connect


@pytest.fixture(scope="session")
def seed_db(tmp_path_factory):
    """The whole research cache ingested once (about 30 s); skips when the cache isn't there."""
    needs_seed()
    conn = connect(tmp_path_factory.mktemp("seed") / "history.sqlite")
    ingest(conn, SEED_HTML)
    yield conn
    conn.close()
