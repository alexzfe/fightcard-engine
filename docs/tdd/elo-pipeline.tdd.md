# TDD evidence: fighter rating roll-forward (`score` stage)

- **Source plan:** `docs/ELO.md` (§ Contract) and the orchestrator's pipeline brief.
- **Replaces:** the K=32 roll-forward of the snapshot `elo` (`33c651f` / `4c795d5`). The finish model's own `elo` feature is unchanged.
- Journeys were derived during this run.

## User journeys

1. As a fan, I want each corner to show how good the fighter is **today** (decayed for layoffs, on a scale where an active established fighter averages 1500), so that the number means something across eras.
2. As a fan, I want a P4P rank, a career peak, and "provisional" / "inactive" tags, so that I can read the number in context.
3. As the operator, I want `score` to keep scoring finish tiers and exit 0 when the research repo hasn't shipped `fighter_rating.parquet` yet, so that deploys aren't blocked.
4. As the research builder, I want the roll-forward core to take a list of bouts and return per-bout ratings, so that both repos can be tested against the same golden cases (`rating_cases.json`).

## Layout

| Module | Role |
|---|---|
| `score/elo.py` | `k_factor`, `credit` (S-DEC 0.6, M-DEC 0.8, NC → None), `decay` (lazy, continuous, 0.9 a year), `roll_forward(states, bouts) -> [Step]` (same-day batching on pre-day ratings and n; NC counts toward n and resets the clock; peak from the 5th UFC bout; debutant opponents start at 1500, n=0), `states_from_export`, `fighter_ratings` (newer bouts from `recent_fights` after `as_of`, deduplicated; null rule; decay to now; anchor; P4P; tags). |
| `score/bundle.py` | Loads optional `fighter_rating.parquet` into `Bundle.ratings` (None when absent). The required files are unchanged. |
| `score/stage.py` | Writes the five Fighter fields from `fighter_ratings(..., now=--now date)`. Tiers are computed exactly as before. |
| `models.py` | `Fighter.elo` (re-documented), `elo_peak`, `p4p_rank`, `elo_provisional`, `elo_inactive`. |

## Task report

| Step | Command | Result |
|---|---|---|
| Baseline | `cd pipeline && uv run pytest -q` | `254 passed, 1 deselected` |
| RED (`0a2b3ce`) | `uv run pytest -q tests/score` | `42 failed, 27 passed`. 39 fail with `NotImplementedError` from the new stubs (`k_factor`, `credit`, `decay`, `roll_forward`, `fighter_ratings`, `State`, `Bout`). The stage tests fail with `assert 1520 == 1376` / `1617 == 1612` (the old K=32 snapshot value) and `AttributeError: 'Fighter' object has no attribute 'elo_peak'`. These are the intended missing behaviours. |
| GREEN (`51a1c90`) | `uv run pytest -q` | `283 passed, 1 deselected` |
| Coverage | `uv run pytest -q tests/score --cov=fightcard.score --cov-report=term-missing` | `elo.py` 100% (152 stmts), `bundle.py` 100%, `stage.py` 99% (line 48 is the pre-existing `_guard` "not in fighters.json" branch) |
| Smoke, no export | `fightcard --data <scratch copy of data> score --model-dir model` | `49 bouts with a tier, 0 stale fighter entries, 0 fighters with an Elo`, exit 0. Every fighter gets `elo/elo_peak/p4p_rank` null and both tags false. |
| Smoke, synthetic export | Same, with a scratch `fighter_rating.parquet` built from the snapshot's `elo` / `last_bout_date` and `--now 2026-10-25` | `133 fighters with an Elo` (1 of 134 unrated), exit 0. Ranks, tags and `inactive` populate (e.g. Nunes is `inactive`). The values are not meaningful because the input was synthetic. |

## Test specification

| # | Guarantee | Test (`pipeline/tests/score/`) | Type | Result |
|---|---|---|---|---|
| 1 | `K = 275/(n+5)^0.4`, no floor | `test_elo.py::test_k_shrinks_with_prior_bouts_and_has_no_floor` | unit | PASS |
| 2 | W by result and method: S-DEC 0.6 / 0.4, M-DEC 0.8 / 0.2, draws 0.5 whatever the method, KO/SUB/U-DEC/DQ 1, NC/Overturned/CNC no update | `test_credit_by_result_and_method` (14 cases) | unit | PASS |
| 3 | Decay is `1500 + (R−1500)·0.9^(days/365.25)`, none on the same day or with no prior bout | `test_decay_is_ten_percent_a_year_toward_1500_and_continuous` | unit | PASS |
| 4 | A bout decays both sides to the day, uses each side's own K, and stores the undecayed post-bout value; n, n_ufc and the last date advance | `test_one_bout_uses_each_sides_own_k_and_decays_to_the_day` | unit | PASS |
| 5 | An opponent missing from the export starts at 1500 with n=0 | `test_opponent_missing_from_states_starts_as_a_debutant`, `test_debut_since_the_export_starts_at_1500` | unit/integration | PASS |
| 6 | A heavy favourite loses points on a split win | `test_heavy_favourite_can_lose_points_on_a_split_win` | unit | PASS |
| 7 | NC: no rating change, but it counts toward n and n_ufc and resets the decay clock | `test_no_contest_changes_no_rating_but_counts_toward_n_and_resets_the_clock` | unit | PASS |
| 8 | Same day: every bout uses pre-day ratings and n; changes sum; n advances after the day | `test_same_day_bouts_all_use_pre_day_ratings_and_n` | unit | PASS |
| 9 | Days apply in date order whatever the input order; decay between them | `test_days_apply_in_date_order_whatever_the_input_order` | unit | PASS |
| 10 | Peak starts at the 5th UFC bout, only rises; a DWCS / Road to UFC bout doesn't count as UFC | `test_peak_counts_from_the_fifth_ufc_bout_and_only_rises`, `test_contender_series_and_road_to_ufc_are_not_ufc_bouts`, `test_newer_ufc_bouts_count_toward_tags_and_peak` | unit/integration | PASS |
| 11 | No export → every fighter unrated; stage exits 0 and tiers still score | `test_without_a_rating_export_every_fighter_is_unrated`, `test_score_stage.py::test_without_a_rating_export_fighters_are_unrated_and_tiers_still_score` | unit/integration | PASS |
| 12 | Anchor: mean of decayed ratings over n≥3 with a bout in 730 days is 1500; applied to every value, including peak | `test_anchor_puts_the_mean_of_active_established_fighters_at_1500`, `test_no_new_fights_shows_the_anchored_decayed_rating`, `test_peak_is_shown_on_the_anchored_scale_undecayed` | integration | PASS |
| 13 | Tags: provisional under 3 UFC bouts; inactive after 365 days (365 is still active) | `test_tags_provisional_under_three_ufc_bouts_and_inactive_after_a_year` | integration | PASS |
| 14 | P4P: top 15 men, top 10 women, ≥5 UFC bouts and active, gender required, ranked over the whole export (not just the card) | `test_p4p_ranks_top_15_men_and_top_10_women_among_eligible`, `test_ranking_covers_the_whole_export_not_just_the_card` | integration | PASS |
| 15 | Newer bouts come from `recent_fights` after `as_of`, each counted once though both fighters list it; split credit read from `PastFight.method` | `test_a_card_fighters_newer_win_moves_both_sides_once`, `test_fights_on_or_before_as_of_are_already_in_the_export`, `test_split_decision_from_the_method_and_debutant_opponent` | integration | PASS |
| 16 | Null rule kept: unseen bouts or a missing opponent id → unrated and left out of the ranking; not in the export with no newer bouts → unrated | `test_missing_newer_fights_nulls_the_fighter_and_drops_them_from_the_ranking`, `test_newer_fight_without_opponent_id_nulls_the_fighter`, `test_not_in_the_export_and_no_newer_bouts_is_unrated` | integration | PASS |
| 17 | Stage writes all five fields into fighters.json, follows `--now`, and reruns are byte-identical | `test_score_stage.py::test_writes_rating_fields_into_fighters_json`, `test_decay_follows_now` | integration | PASS |
| 18 | Finish-tier scoring is unchanged | existing `test_score_stage.py` tier tests, `test_score_parity.py` | integration | PASS |

## Interpretations (Contract ambiguities)

- **What counts as a UFC bout** for a post-`as_of` bout: any event whose name doesn't start with `DWCS` or `Road to UFC`. Now pinned in ELO.md, matched **case-insensitively** since cycle 2.
- **Peak:** cycle 1 updated it only on a day with a UFC bout. Superseded in cycle 2: ELO.md now pins any bout once `n_ufc ≥ 5`.
- **NC and decay:** an NC bout decays the stored rating to the bout day (the same-day rule decays each fighter to the day before applying a zero change).
- **Anchor population** includes unrated (null-rule) fighters, using the bouts we know; they are only hidden and unranked. Debutant opponents added by the roll-forward are part of the population.
- **Unrated fighters** get every field null / false, including peak and tags.
- **`Step.post_a/post_b`** is the rating after the whole day (it differs from `pre + delta` only when a fighter has two bouts on one day).

## Coverage and known gaps

- The vendored `model/` has no `fighter_rating.parquet` yet, so the live site shows no ratings until the new export is copied in (not done here: `model/` is vendored by hand, and regenerated data isn't committed from this branch).
- Known limitation (Contract): fighters off our cards who fought after `as_of` stay stale until the next re-export.

## Cycle 2: research parity, case-insensitive UFC names, pinned peak (2026-10-25)

- **Why:** the research export landed with golden cases. ufcstats names the 2026 events `Road To UFC 5.x`, which the case-sensitive prefix counted as UFC. ELO.md now pins the peak to any bout once the fighter has ≥5 UFC bouts.
- **Fixture:** `pipeline/tests/score/fixtures/rating_cases.json`, vendored byte-identical (`cmp`) from the research `tests/fixtures/rating_cases.json` and the export output. Schema: the research `export/rating.py` docstring.
- **RED (`0ac7aba`):** `uv run pytest -q tests/score` gave `18 failed, 69 passed`:
  - 10 golden cases: `TypeError` on `Bout(weight_class=...)` (no gender inference yet).
  - The params check: missing `DAYS_PER_YEAR` / `K_*` / `NO_UPDATE_METHODS`.
  - `credit` for `win`/`loss` Overturned and `draw` CNC: gave 1 / 0 / 0.5, want no update.
  - Peak after a non-UFC bout once `n_ufc ≥ 5`.
  - `Road To UFC 5.2`, `ROAD TO UFC 5.3`, `dwcs 10.7` counted as UFC.
- **GREEN (`0e40cbc`):** `uv run pytest -q` gave **`301 passed, 1 deselected`**. `elo.py` coverage is 100% (169 stmts). Changes:
  - CNC/Overturned never update, whatever the recorded result (ELO.md: "count as NC").
  - `Bout.weight_class` sets `State.gender`: any Women's → F, else a named division → M; catch/open weight or none leaves it.
  - `Step` gains `e_a`, `w_a`, `peak_a`, `peak_b`, so it is a full golden log row.
  - The peak updates on any bout once `n_ufc ≥ 5`.
  - The UFC prefix match is lower-cased.
  - Params are constants checked against `rating_cases.json` `params`.
- **Test fixed during GREEN:** the new peak assertion in `test_peak_counts_from_the_fifth_ufc_bout_and_only_rises` was wrong. It followed a loss, so one win against `b` didn't reach a new high. The non-UFC win now comes against a 2200-rated opponent, so it does set a new high. Under the old UFC-only rule the peak would stay at `top`, so the test still fails there.
- **Smoke with the real export** (`--model-dir <export-out>`, a scratch copy of data/, `--now 2026-10-25`): `49 bouts with a tier, 0 stale fighter entries, 133 fighters with an Elo`, exit 0. Top of the card: Yan 1764 (#3 P4P), Volkanovski 1724 (peak 1808, #8), Volkov 1721 (#9). Women ranked: Suarez #5, Jandiroba #10. The one unrated fighter is a UFC debutant with no in-scope bouts.

| # | Guarantee | Test | Type | Result |
|---|---|---|---|---|
| 19 | The roll-forward reproduces every research golden case: per-bout `pre/k/e/w/delta/post/peak` and final `rating/n/n_ufc/last/peak/gender` to 1e-9 (debut, decay_gap, same_day, split_majority, favourite_split_win, draw, nc_counts_n, cnc_overturned, peak_fifth_ufc, gender) | `test_rating_parity.py::test_golden_case[*]` (10) | parity | PASS |
| 20 | Our formula and display constants equal the export's `params` | `test_rating_parity.py::test_format_and_params_match_ours` | parity | PASS |
| 21 | CNC / Overturned never update, whatever the recorded result | `test_credit_by_result_and_method[win-Overturned…, loss-Overturned…, draw-CNC…]` | unit | PASS |
| 22 | DWCS / Road to UFC in any letter case are not UFC bouts | `test_contender_series_and_road_to_ufc_are_not_ufc_bouts_in_any_case[*]` (5) | integration | PASS |
| 23 | Once the fighter has ≥5 UFC bouts, any bout (UFC or not) can set the peak | `test_peak_counts_from_the_fifth_ufc_bout_and_only_rises`, golden `peak_fifth_ufc` | unit/parity | PASS |
