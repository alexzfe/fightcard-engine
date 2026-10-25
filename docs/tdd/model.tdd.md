# TDD evidence: model (finish-tier export + score stage)

- **Source plan:** `PLAN.md`: milestones 4 (model export) and 5 (scoring), and the "Finish model" section. Brief: `briefs/model.md`.
- **Repos:**
  - `fight-auto-research`: worktree `../fight-auto-research-export`, branch `feat/export`, not pushed.
  - this repo: branch `feat/v1`.

## User journeys

1. As the site owner, I want `python -m export` to produce a self-contained model bundle, so the site can score fights without the research repo or its 13 GB raw cache.
2. As a visitor, I want a High/Medium/Low badge whose tiers really mean different finish rates.
3. As a visitor, I see `—` instead of a misleading badge when a fighter has no UFC history, or has fought since the model was exported.
4. As a visitor, I see past events keep the badge they had going in, so results don't rewrite the prediction.
5. As the site owner, I want stale fighters listed so CI can open the "Re-export finish model" issue.

## Task report

### Part A: export (fight-auto-research `feat/export`)

| commit | stage | evidence |
|---|---|---|
| `de04697` | RED | `uv run pytest tests/test_export.py`: 9 failed, 3 errors, all `NotImplementedError` from stubs |
| `1416cb0` | GREEN | `uv run pytest tests/test_export.py`: 12 passed (parity on 60 synthetic bouts) |
| `18f9d4c` | RED | `test_export_fails_when_fighter_records_postdate_the_build` fails: DID NOT RAISE |
| `d8131cd` | GREEN | 13 passed |
| `d623987` | RED | `test_export_writes_all_files`: manifest `build.code_version` was None |
| `b6b7f1b` | GREEN | 13 passed |
| `d179cf2` | coverage | CLI exit-code test; `uv run --with pytest-cov pytest tests/test_export.py --cov=export`: 14 passed, **97%** |

- **Full research suite:** `uv run pytest`: **219 passed, 1 skipped** (205 before plus 14 new).
- **Real export:** `uv run python -m export --data-dir ~/Projects/fight-auto-research/data --out …` took 35 s.
  - Output: `snapshot parity ok on 200 bouts`; `low <= 0.4285 < medium <= 0.5562 < high`; finish rates 0.368 / 0.480 / 0.624.
  - Re-running from the final head gave byte-identical model, thresholds, snapshot, spec and examples.

### Part B: score stage (this repo)

| commit | stage | evidence |
|---|---|---|
| `51249e4` | setup | vendored `model/` (needed by the parity test) |
| `ce7d986` | RED | `uv run --project pipeline pytest pipeline/tests/score`: 24 failed, all `NotImplementedError` |
| `5e7695c` | GREEN | 24 passed, including `examples.json` parity at 1e-9 |
| `a1847c1` | refactor | dropped combiners the export never emits; error-path tests. 27 passed, coverage **99%** |
| `1627cee` | docs | `model/README.md`, manifest from the final research head |

- **Whole pipeline suite:** `uv run --project pipeline pytest pipeline/tests`: 182 passed, at the time of the run.
- **Sanity check:** I parsed `pipeline/tests/fixtures/ufcstats/event_detail_upcoming.html` with the scraper.
  - All 13 weight classes, the city, state and country are known model levels.
  - All 26 fighters are in the snapshot.

## Test specification

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| 1 | Rows assembled from snapshots taken the day before a bout equal the build's `features.parquet` row, for every feature column | `tests/test_export.py::test_snapshot_parity_with_build_rows` (60 synthetic bouts); the export re-checks this on 200 real bouts on every run | integration | PASS |
| 2 | Thresholds are the OOS tertiles; per-tier finish rates are recorded | `test_tier_thresholds_are_tertiles_with_increasing_rates` | unit | PASS |
| 3 | The export fails unless rates strictly increase low → medium → high | `test_tier_thresholds_fail_unless_rates_strictly_increase` | unit | PASS |
| 4 | A feature that depends on the bout date without a snapshot recipe fails the export | `test_date_dependent_feature_without_recipe_fails`, `test_baseline_features_pass_date_invariance` | unit | PASS |
| 5 | The snapshot has ufcstats id, name, W/L/D/NC record, UFC bout count and only the recent bouts a future 2-year window can see | `test_snapshot_columns_and_records` | unit | PASS |
| 6 | The export writes all files; manifest has as_of, version, research sha and build provenance | `test_export_writes_all_files`, `test_exported_thresholds_monotone` | e2e | PASS |
| 7 | Booster, spec and snapshot alone (no build code) reproduce the export's predictions | `test_examples_reproduce_from_files_alone` | e2e | PASS |
| 8 | A stale `features.parquet` fails the export with a "rebuild" hint | `test_export_fails_when_features_table_is_stale` | e2e | PASS |
| 9 | A raw fighter CSV that changed after the build is refused, since its records could hide staleness | `test_export_fails_when_fighter_records_postdate_the_build` | unit | PASS |
| 10 | The site scorer reproduces the research predictions and tiers for `model/examples.json` | `pipeline/tests/score/test_score_parity.py` | integration (cross-repo) | PASS |
| 11 | Static, date-recipe and order-free combiners, age bounds, and unknown-category → NaN all work | `test_score_features.py` | unit | PASS |
| 12 | Bout context covers location split, gender, 5 rounds for main event or title, and year | `test_bout_context`, `test_parse_location[*]` | unit | PASS |
| 13 | Tier boundaries are inclusive upper | `test_tier_of_boundaries` | unit | PASS |
| 14 | No-history guard: 0 UFC bouts, or not in the snapshot → `null` | `test_scores_upcoming_fights_and_applies_no_history_guard` | integration | PASS |
| 15 | Staleness guard: record mismatch → `null` plus `stale_fighters.json` entry `{event_id, fighter_id, name}`; the file is removed when nothing is stale | `test_stale_record_nulls_tier_and_is_listed` | integration | PASS |
| 16 | Freezing: event date ≤ today (UTC) sets `tier_frozen_at` once; frozen tiers never change, including null | `test_past_event_tiers_are_frozen_once`, `test_frozen_null_tier_stays_null` | integration | PASS |
| 17 | Freezing after results keeps the pre-fight tier and doesn't flag those fighters stale | `test_freezing_after_results_keeps_the_pre_fight_tier` | integration | PASS |
| 18 | `index.model` comes from `manifest.json` | `test_index_model_from_manifest` | integration | PASS |
| 19 | A missing model dir → exit 1; defaults are `model/` and the current UTC time; reruns are byte-stable | `test_missing_model_dir_fails`, `test_default_model_dir_and_now`, `test_unchanged_run_rewrites_identical_files` | integration | PASS |

## Coverage and known gaps

- **Coverage:**
  - `export`: 97%. Uncovered: an empty-fold guard and the missing-booster-attribute error.
  - `fightcard.score`: 99%. Uncovered: the `fighters.json` entry-missing branch.
- **Scheduled rounds** are inferred: 5 for the main event or a title fight, otherwise 3. ufcstats doesn't list scheduled rounds for upcoming bouts, so a non-title 5-round co-main is scored as 3 rounds.
- **Training includes the locked test window.** The final model is trained on every eligible bout, including those from the research harness's locked test window (from 2025-10-01), for production use. The export reports no metric on that window, since OOS thresholds use only the pre-test scored folds, and writes nothing the harness reads. Seal and lock code is untouched.
- **Thresholds come from fold models;** the final model is trained on more data, so its prediction distribution may drift slightly from the tertiles.
