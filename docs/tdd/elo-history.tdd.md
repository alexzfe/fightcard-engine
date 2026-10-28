# TDD evidence: Elo at fight time

**Source plan:** the shared "Elo at fight time" plan agreed 2026-10-28 (orchestrator scratchpad, not in the repo):
completed fights show each fighter's Elo going in, the bout's own change and their P4P rank going in, built on a
reusable "ratings as of any date" timeline. **Branch:** `feat/elo-history`.

## Phase 1: the timeline

### User journeys
- As the engine, I want each fighter's rating, counts and P4P lists as of going into any day, so the fight-time Elo
  (Phase 2) and later features (an Elo chart, historical rankings, an event archive) are queries, not new passes.
- As a visitor, I want a tournament-day fighter's bouts to each show their own change, not the day's total.

### Checkpoints
| Stage | Commit | Command | Result |
|---|---|---|---|
| RED | 568fcd8 | `uv run pytest -q` | collection error: `No module named 'fightcard.history.timeline'`; without the new file 2 failed (`test_history_pass_is_the_one_pass_…`: a 4-field result; `test_every_suite_collects_from_the_repo_root`: the collection error), 1028 passed, 1 skipped |
| GREEN | a2fe697 | `uv run pytest -q` | 1056 passed, 1 skipped; `uv run pytest -q -m seed tests/history` 7 passed |

`rating.history_pass` now returns a `HistoryPass` NamedTuple (`ratings`, `bouts`, `names`, `log`): `rate`'s per-bout
log is kept instead of discarded. `score`, `images` and `fightcard history rankings` unpack the four fields; each still
runs the pass once. `fightcard.history.timeline.Timeline` (`Timeline.from_pass(hp)`) is built from that one pass.
Nothing reads it yet (Phase 2 does).

Mutation check: with `state` / `ratings` counting the day itself (`bisect_left(...) + (day in days)`), 10 of the
timeline tests fail.

### Guarantees
| # | What is guaranteed | Test |
|---|---|---|
| 1 | Going into D equals a fresh pass over the bouts strictly before D: `ratings(D)` == `rate`'s rows (rating, last bout date, n, n_ufc, peak, gender), `state(f, D)` == those decayed to D, `p4p(D)` == `rankings.p4p` as of D-1. Synthetic league (10 days incl. a tournament day, the first card's day, before and long after) and the frozen parity bouts (8 days incl. UFC 1 1993-11-12 and its next day) | `test_timeline_equals_a_fresh_pass_before_the_day_on_a_synthetic_league`, `…_on_the_frozen_parity_bouts` |
| 2 | Same against `history_pass(as_of=D-1)` on the full store (`-m seed`): UFC 1, the day after, a day with no bouts (2010-12-25), a recent card, the last card and the day after it; and the day after the last bout equals the full pass | `test_timeline_equals_history_pass_as_of_the_day_before_on_the_full_store` |
| 3 | No bout before D → `state` None, empty ratings, empty P4P | `test_before_any_bout_nobody_is_rated` |
| 4 | `state` is decayed to the day; it is the next bout's `pre` | `test_the_state_is_decayed_to_the_day_going_in` |
| 5 | `bout(fight_id)` = each fighter's log `pre` and own `delta`; `pre` = `state` going into the day (1500 for a debutant); unknown fight → None | `test_bout_gives_each_fighters_pre_and_own_delta_from_the_log` |
| 6 | Tournament day: four bouts from one pre-day rating, four different deltas, summing to post - pre; `post` is the end-of-day value on all four; n advances by 4 after the day | `test_a_tournament_day_gives_each_bout_its_own_delta_summing_to_the_days_change` |
| 7 | NC, CNC, Overturned → delta 0 | `test_no_update_bouts_have_a_zero_delta` |
| 8 | `series` = the fighter's bouts in date order with opponent, pre, delta, post | `test_series_is_the_fighters_bouts_in_date_order` |
| 9 | `history_pass` keeps `rate`'s log; the timeline builds from it | `test_history_pass_keeps_rates_log_…`, `test_history_pass_is_the_one_pass_…` |

### Timing (full store, 11,436 bouts, 4,150 fighters, three runs)
| Step | Time |
|---|---|
| `history_pass` (the one pass) | 0.06–0.07 s |
| `Timeline.from_pass` | 0.04–0.07 s |
| `p4p(day)` | ~15 ms per day (20 days 0.30–0.34 s), cached per day |

`p4p(day)` is as of D-1 (bouts before D, decayed to the day before, as `fightcard history rankings --as-of D-1`
gives), while `state(f, D)` is decayed to D itself, the rating the fighter carried into the bout.
