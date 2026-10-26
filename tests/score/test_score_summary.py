"""index.model's summary of the vendored export: what the how-it-works page quotes."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from fightcard.score.bundle import load_bundle
from fightcard.score.summary import GROUPS, model_summary

MODEL_DIR = Path(__file__).resolve().parents[2] / "model"


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(MODEL_DIR)


def test_quotes_the_manifest_and_thresholds(bundle):
    s = model_summary(bundle)
    t, m = bundle.thresholds, bundle.manifest
    assert (s["as_of"], s["version"], s["train_rows"]) == (m["as_of"], m["version"], m["train"]["rows"])
    assert s["trees"] == bundle.booster.num_trees()
    assert (s["low_max"], s["medium_max"]) == (t["low_max"], t["medium_max"])
    assert s["oos"] == {"bouts": t["oos"]["n"], "log_loss": t["oos"]["log_loss"],
                        "constant_log_loss": t["oos"]["constant_log_loss"]}
    for tier in ("low", "medium", "high"):
        assert s["tiers"][tier] == {"bouts": t["tier_counts"][tier], "finish_rate": t["tier_finish_rates"][tier],
                                    "mean_predicted": t["tier_mean_pred"][tier]}


def test_importance_has_every_group_largest_first_adding_to_100(bundle):
    imp = model_summary(bundle)["importance"]
    assert sorted(g["group"] for g in imp) == sorted(GROUPS)
    shares = [g["share"] for g in imp]
    assert shares == sorted(shares, reverse=True)
    assert sum(shares) == pytest.approx(100, abs=0.15)


def test_importance_matches_the_shares_worked_out_by_hand_for_the_2026_10_14_export(bundle):
    if bundle.manifest["version"] != "2026-10-14+5006318a":
        pytest.skip("pinned to the export whose shares were first worked out by hand")
    shares = {g["group"]: g["share"] for g in model_summary(bundle)["importance"]}
    assert shares == {"fight_endings": 19.9, "context": 17.8, "grappling": 17.1, "striking": 15.5,
                      "physical": 12.8, "experience": 9.5, "form": 7.4}


def test_a_feature_without_a_group_fails(bundle):
    b = copy.copy(bundle)
    b.spec = copy.deepcopy(bundle.spec)
    b.spec["features"][0]["name"] = "brand_new_feature"
    with pytest.raises(ValueError, match="brand_new_feature"):
        model_summary(b)
