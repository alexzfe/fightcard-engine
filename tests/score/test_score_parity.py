"""The vendored model/ scores its own examples exactly as the research export did."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

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


# fightcard.score.parity: the same check, as a function a model pull can run on a candidate bundle.

def test_parity_failures_is_empty_for_the_vendored_model():
    from fightcard.score.parity import parity_failures
    examples = json.loads((MODEL_DIR / "examples.json").read_text())
    assert parity_failures(load_bundle(MODEL_DIR), examples) == []


def test_parity_failures_names_an_example_whose_probability_moved():
    from fightcard.score.parity import parity_failures
    examples = json.loads((MODEL_DIR / "examples.json").read_text())
    examples[2]["p_finish"] += 0.01
    failures = parity_failures(load_bundle(MODEL_DIR), examples)
    assert len(failures) == 1 and "example 2" in failures[0] and "p_finish" in failures[0]


def test_parity_failures_names_an_example_whose_tier_moved():
    from fightcard.score.parity import parity_failures
    examples = json.loads((MODEL_DIR / "examples.json").read_text())
    examples[0]["tier"] = {"low": "high"}.get(examples[0]["tier"], "low")
    failures = parity_failures(load_bundle(MODEL_DIR), examples)
    assert len(failures) == 1 and "example 0" in failures[0] and "tier" in failures[0]


@pytest.mark.parametrize("examples", [[], [{"red": "x"}], "nope"])
def test_parity_failures_rejects_an_unusable_examples_file(examples):
    from fightcard.score.parity import parity_failures
    assert parity_failures(load_bundle(MODEL_DIR), examples)


@pytest.mark.parametrize("value", ["0.5", None, [0.5], {"p": 0.5}, True])
def test_parity_failures_holds_on_a_malformed_p_finish(value):
    from fightcard.score.parity import parity_failures
    examples = json.loads((MODEL_DIR / "examples.json").read_text())
    examples[1]["p_finish"] = value
    failures = parity_failures(load_bundle(MODEL_DIR), examples)
    assert failures and all(isinstance(f, str) for f in failures)


@pytest.mark.parametrize("tier", [None, ["low"], {"t": 1}, 3])
def test_parity_failures_holds_on_a_malformed_tier(tier):
    from fightcard.score.parity import parity_failures
    examples = json.loads((MODEL_DIR / "examples.json").read_text())
    examples[1]["tier"] = tier
    assert parity_failures(load_bundle(MODEL_DIR), examples)


@pytest.mark.parametrize("field,value", [("context", "nope"), ("context", None), ("context", {"x": object}),
                                         ("date", 5), ("date", None), ("red", None), ("red", ["a"])])
def test_parity_failures_holds_on_a_malformed_bout(field, value):
    from fightcard.score.parity import parity_failures
    examples = json.loads((MODEL_DIR / "examples.json").read_text())
    examples[1][field] = value
    failures = parity_failures(load_bundle(MODEL_DIR), examples)
    assert failures and all(isinstance(f, str) for f in failures)


def test_parity_failures_holds_when_the_booster_cannot_score(monkeypatch):
    import lightgbm as lgb

    from fightcard.score.parity import parity_failures
    bundle = load_bundle(MODEL_DIR)
    examples = json.loads((MODEL_DIR / "examples.json").read_text())

    def broken(self, rows):
        raise lgb.basic.LightGBMError("the number of features in data is not the same as it was in training data")
    monkeypatch.setattr(type(bundle), "predict", broken)
    failures = parity_failures(bundle, examples)
    assert failures and "LightGBMError" in failures[0]
