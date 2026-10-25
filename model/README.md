# model/ — vendored finish-tier model

Exported from `~/Projects/fight-auto-research` by `python -m export` (branch `feat/export`), then copied here by hand.
`fightcard score` (`pipeline/src/fightcard/score/`) reads it. The only runtime dependencies are lightgbm, pandas and pyarrow, and it never needs the research repo.

Current export: **as of 2026-10-14** (build data through the 2026-10-10 card), version `2026-10-14+5006318a`, research sha `d179cf2`.

| file | what |
|---|---|
| `model.txt` | LightGBM booster (research `model.py`, 300 rounds). Trained on all 8,340 eligible UFC bouts from 2005 on. |
| `thresholds.json` | Tier cut points and their evidence (below). |
| `fighter_snapshot.parquet` | One row per ufcstats fighter (4,628; 2,769 with UFC bouts). |
| `feature_spec.json` | Ordered model columns, categorical levels, and how to build each feature from the snapshot. |
| `manifest.json` | `as_of`, `version`, `research_sha`, training rows, build provenance, lightgbm version. With a rating export it also has `"rating": {"format_version": 1, "params": {...}}`. |
| `fighter_rating.parquet` | **Optional.** The fighter rating as of `as_of` (below). Without it, `score` leaves every fighter unrated and still scores tiers. Not in the current export yet. |
| `examples.json` | 12 bouts with the export's own `p_finish` and tier. `pipeline/tests/score/test_score_parity.py` re-scores them, which keeps the scorer and the research code in lockstep. |

## Tiers

`low` if `p <= low_max`, `medium` if `p <= medium_max`, else `high`.

The cut points are the tertiles of **out-of-sample** predictions on 4,868 bouts: the research harness's ten scored folds, Oct 2015 – Sep 2025. Each fold is predicted by a model trained only on earlier bouts. The export fails unless the actual finish rates strictly increase from low to high.

| tier | p range | bouts | actual finish rate | mean predicted |
|---|---|---|---|---|
| low | ≤ 0.4285 | 1,623 | 0.368 | 0.348 |
| medium | 0.4285 – 0.5562 | 1,622 | 0.480 | 0.490 |
| high | > 0.5562 | 1,623 | 0.624 | 0.647 |

OOS log loss is 0.665, against 0.693 for a constant base rate. No fold touches the research repo's locked test window (from 2025-10-01).

## fighter_snapshot.parquet

Each row holds a fighter's state going into any bout dated after `as_of`. History is every in-scope ufcstats bout up to `as_of`: UFC plus full-card other promotions.

| column | meaning |
|---|---|
| `fighter_id` | ufcstats fighter id (same as `data/fighters.json` keys) |
| `name` | as in the research raw data |
| `record_w`, `record_l`, `record_d`, `record_nc` | ufcstats career record when the build's raw data was scraped. **Staleness guard:** if `data/fighters.json` shows a different record, the fighter has fought since the export, so the bout gets `—` and the fighter is listed in `data/stale_fighters.json`. |
| `n_ufc_bouts` | UFC bouts in the history. **No-history guard:** 0 (or not in the snapshot) → `—`. |
| `dob`, `first_bout_date`, `last_bout_date` | `YYYY-MM-DD` or null; inputs to `age`, `career_days` and `layoff_days` |
| `recent_dates`, `recent_finish_loss`, `recent_ko_loss` | bouts in the last 730 days before `as_of`+1, as parallel lists; inputs to the two-year window features |
| `elo` | Elo going into the next bout (K=32, start 1500; research `build/toolkit.py`). A finish-model feature only; the site's displayed rating comes from `fighter_rating.parquet`. |
| every other column | a feature's per-fighter value (e.g. `elo`, `slpm`, `stance`). It doesn't depend on the bout date as long as the fighter hasn't fought again. |

## fighter_rating.parquet

The fighter rating from `docs/ELO.md` (n-based K, split/majority credit, lazy decay), computed by the research repo
over full history. One row per fighter with at least one bout in scope, as of `as_of`.

| column | meaning |
|---|---|
| `fighter_id` | ufcstats fighter id |
| `rating` | raw post-bout rating after the last bout (undecayed) |
| `last_bout_date` | `YYYY-MM-DD` |
| `n_bouts` | bouts in scope, NC included (drives K) |
| `n_ufc_bouts` | UFC-event bouts, NC included (tags, P4P eligibility, peak) |
| `peak` | raw peak: highest post-bout rating from the 5th UFC bout on, undecayed; null until then |
| `gender` | `"M"`, `"F"` or null (from weight classes) |

`score` (`pipeline/src/fightcard/score/elo.py`) rolls it forward over the card fighters' `recent_fights` dated after
`as_of` with the same formula (an opponent not in the export starts at 1500 with n = 0), decays everyone to `--now`,
anchors and ranks, and writes the Fighter rating fields (see `docs/CONTRACT.md`). If the newer bouts found don't
match the record change since the export (record from `fighter_snapshot.parquet`), that fighter is unrated and left
out of the ranking. Fighters off our cards who fought after `as_of` stay stale until the next export.

## feature_spec.json

- `columns`: the booster's input order. First the bout context (`weight_class`, `gender`, `event_city`, `event_state`, `event_country`, `sched_rounds`, `title_fight`, `year`), then the features.
- `categorical`: `{column: [levels]}`. A value is encoded as its index in the list, or NaN if it's missing or unseen.
- `features`: `[{name, kind, combiners, source}]`. Column `<name>_<combiner>` = `combiner(value(red), value(blue))`.
  - Combiners are order-free: `min`, `max`, `sum`, `absdiff`, plus `pair` = `"|".join(sorted(...))` for stance.
  - NaN on either side gives NaN.
  - `source.kind` says how to get `value()` from a snapshot row at the bout date:
    - `static`: the snapshot column itself.
    - `age`: `(date - dob).days / 365.25`, NaN outside `[min, max]`.
    - `days_since`: `(date - column).days`.
    - `window_count`: number of `recent_dates` ≥ `date - days`.
    - `window_sum`: `nansum` of `recent_<column>` over those bouts.

Bout context on the site (`fightcard.score.features.bout_context`):

- **Location:** `Event.location` "City, State, Country" is split into city/state/country; a two-part location has no state.
- **Gender:** `F` for "Women's …" weight classes.
- **Scheduled rounds:** 5 for the main event (first fight on the card) and title fights, otherwise 3. ufcstats doesn't list scheduled rounds on upcoming cards.

## Re-exporting

Do this when the "Re-export finish model" issue opens or the model changes.

1. Refresh the research data: `cd ~/Projects/fight-auto-research`, scrape, then `uv run python -m build`.
2. Export from the research checkout or a worktree that has `export/`:
   ```
   uv run python -m export --data-dir ~/Projects/fight-auto-research/data --out /tmp/finish-model
   ```
   It refuses to write anything when:
   - `features.parquet` doesn't match the current `features.FEATURES`
   - the raw fighter CSV changed after the build
   - a feature depends on the bout date without a snapshot recipe
   - snapshot rows don't reproduce the build's rows on 200 historical bouts
   - the tier finish rates aren't strictly increasing
3. `cp /tmp/finish-model/* model/`
4. Update the numbers in this README, run `uv run --project pipeline pytest pipeline/tests/score`, then commit `model/`.

The next refresh re-scores every unfrozen bout. Tiers already frozen (past events) never change.
