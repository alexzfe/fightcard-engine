from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO / "model"


@pytest.fixture(scope="session")
def real_snapshot() -> pd.DataFrame:
    return pd.read_parquet(MODEL_DIR / "fighter_snapshot.parquet")


@pytest.fixture(scope="session")
def veterans(real_snapshot) -> list[dict]:
    """Snapshot rows of fighters with plenty of UFC bouts (stable picks across re-exports)."""
    rows = real_snapshot[real_snapshot["n_ufc_bouts"] >= 5].sort_values("fighter_id").head(6)
    return rows.to_dict("records")


@pytest.fixture
def model_dir(tmp_path) -> Path:
    """A copy of the vendored model/ that tests may modify (thresholds, snapshot)."""
    d = tmp_path / "model"
    shutil.copytree(MODEL_DIR, d)
    return d

