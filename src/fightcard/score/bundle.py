"""The vendored finish-tier model (model/, see model/README.md)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from fightcard.models import FinishTier
from fightcard.score.features import encode

RATINGS = "fighter_rating.parquet"  # optional: without it every fighter is unrated
FILES = ("model.txt", "thresholds.json", "fighter_snapshot.parquet", "feature_spec.json", "manifest.json")


def tier_of(p: float, thresholds: dict) -> FinishTier:
    if p <= thresholds["low_max"]:
        return "low"
    return "medium" if p <= thresholds["medium_max"] else "high"


@dataclass
class Bundle:
    booster: lgb.Booster
    spec: dict
    thresholds: dict
    manifest: dict
    snapshot: pd.DataFrame  # indexed by fighter_id
    ratings: pd.DataFrame | None = None  # fighter_rating.parquet indexed by fighter_id; None when absent

    def predict(self, rows: list[dict]) -> np.ndarray:
        if not rows:
            return np.empty(0)
        return self.booster.predict(encode(rows, self.spec))

    def tier(self, p: float) -> FinishTier:
        return tier_of(p, self.thresholds)


def load_bundle(model_dir: Path) -> Bundle:
    model_dir = Path(model_dir)
    missing = [f for f in FILES if not (model_dir / f).is_file()]
    if missing:
        raise FileNotFoundError(f"model dir {model_dir} is missing {', '.join(missing)}")
    read = lambda name: json.loads((model_dir / name).read_text())  # noqa: E731
    snapshot = pd.read_parquet(model_dir / "fighter_snapshot.parquet").set_index("fighter_id")
    ratings = None
    if (model_dir / RATINGS).is_file():
        ratings = pd.read_parquet(model_dir / RATINGS).set_index("fighter_id")
    return Bundle(lgb.Booster(model_file=str(model_dir / "model.txt")), read("feature_spec.json"),
                  read("thresholds.json"), read("manifest.json"), snapshot, ratings)
