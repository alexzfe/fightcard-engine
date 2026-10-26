"""The vendored model/ scores its own examples exactly as the research export did."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from fightcard.score.bundle import load_bundle
from fightcard.score.features import bout_row

MODEL_DIR = Path(__file__).resolve().parents[2] / "model"


def test_examples_reproduce_research_predictions():
    bundle = load_bundle(MODEL_DIR)
    examples = json.loads((MODEL_DIR / "examples.json").read_text())
    assert len(examples) >= 5
    rows = [bout_row(bundle.snapshot, bundle.spec, e["red"], e["blue"], e["date"], e["context"]) for e in examples]
    p = bundle.predict(rows)
    assert np.allclose(p, [e["p_finish"] for e in examples], atol=1e-9)
    assert [bundle.tier(v) for v in p] == [e["tier"] for e in examples]


def test_bundle_metadata():
    bundle = load_bundle(MODEL_DIR)
    assert bundle.manifest["as_of"] and bundle.manifest["version"]
    assert bundle.thresholds["low_max"] < bundle.thresholds["medium_max"]
    assert bundle.snapshot.index.name == "fighter_id" and bundle.snapshot.index.is_unique
