"""The vendored export summarised for the how-it-works page, written into index.model by `score`.

Importance is the booster's total split gain, summed per base feature (all its order-free combiner columns)
and per theme in GROUPS, as a percentage rounded to 0.1. Bout-context columns form the "context" theme.
A feature without a theme fails loudly: a model with a new feature needs it placed (and labelled on the site).
"""

from __future__ import annotations

from fightcard.score.bundle import Bundle

CONTEXT = ("weight_class", "gender", "event_city", "event_state", "event_country", "sched_rounds",
           "title_fight", "year")
GROUPS: dict[str, tuple[str, ...]] = {
    "fight_endings": ("decision_rate", "finish_win_rate", "finished_rate", "ko_loss_rate", "finished_2y", "kod_2y"),
    "context": CONTEXT,
    "grappling": ("sub_att_pm", "sub_att_opp_pm", "td_acc", "td_def", "td_att_pm", "td_landed_pm", "ctrl_share",
                  "ctrl_opp_share"),
    "striking": ("slpm", "sapm", "sig_acc", "sig_def", "head_absorbed_pm", "kd_pm", "kd_opp_pm"),
    "physical": ("reach_cm", "height_cm", "age", "stance"),
    "experience": ("minutes_fought", "layoff_days", "career_days", "fights_2y", "n_fights", "n_ufc_fights",
                   "n_stat_fights", "no_history"),
    "form": ("elo", "opp_elo_mean", "win_rate", "win_streak", "loss_streak", "last1_result", "last2_result",
             "last3_result", "last1_ko", "last2_ko", "last3_ko", "last1_sub", "last2_sub", "last3_sub"),
}
TIERS = ("low", "medium", "high")


def importance(bundle: Bundle) -> list[dict]:
    group_of = {name: g for g, names in GROUPS.items() for name in names}
    unplaced = [f["name"] for f in bundle.spec["features"] if f["name"] not in group_of]
    if unplaced:
        raise ValueError(f"features without an importance group in fightcard.score.summary: {', '.join(unplaced)}")
    column_of = {f"{f['name']}_{c}": f["name"] for f in bundle.spec["features"] for c in f["combiners"]}
    column_of.update({c: c for c in CONTEXT})
    columns = bundle.spec["columns"]
    unknown = [c for c in columns if c not in column_of]
    if unknown:
        raise ValueError(f"model columns that are neither a feature nor bout context: {', '.join(unknown)}")
    gain = dict.fromkeys(GROUPS, 0.0)
    for col, g in zip(columns, bundle.booster.feature_importance("gain"), strict=True):
        gain[group_of[column_of[col]]] += float(g)
    total = sum(gain.values())
    shares = [{"group": g, "share": round(100 * v / total, 1)} for g, v in gain.items()]
    return sorted(shares, key=lambda s: -s["share"])


def model_summary(bundle: Bundle) -> dict:
    """index.model's fields: identity, training size, tier cut points, OOS evidence, per-tier stats, importance."""
    t, m = bundle.thresholds, bundle.manifest
    return {
        "as_of": m["as_of"],
        "version": m["version"],
        "train_rows": m["train"]["rows"],
        "trees": bundle.booster.num_trees(),
        "low_max": t["low_max"],
        "medium_max": t["medium_max"],
        "oos": {"bouts": t["oos"]["n"], "log_loss": t["oos"]["log_loss"],
                "constant_log_loss": t["oos"]["constant_log_loss"]},
        "tiers": {k: {"bouts": t["tier_counts"][k], "finish_rate": t["tier_finish_rates"][k],
                      "mean_predicted": t["tier_mean_pred"][k]} for k in TIERS},
        "importance": importance(bundle),
    }
