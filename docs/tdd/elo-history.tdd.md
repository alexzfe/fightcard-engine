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

## Phase 2: event fields + contract

### User journeys
- As a visitor, I want a completed fight to show each fighter's Elo going in, the bout's own change and their P4P
  rank going in ("1612 (+26) · #4 P4P"), so I can see what the result meant at the time.
- As a consumer of `/v1/`, I want it as an optional, strictly typed `Fight.elo` announced by `contract_version`
  `"1.3"`, so older events still validate and I know when it appears.

### Checkpoints
| Stage | Commit | Command | Result |
|---|---|---|---|
| RED | 75b4e64 | `uv run pytest -q` | 33 failed (7 score: no `Fight.elo`; 4 publish: version 1.2, no `elo` in the schema; 3 strict-int refusals; 18 docs; `test_score_tests_find_the_vendored_model_at_the_repo_root`, which reruns the score suite), 1053 passed, 1 skipped |
| GREEN | a4a57ea | `uv run pytest -q` | 1086 passed, 1 skipped; `uv run pytest -q -m seed tests/history` 7 passed |

`models.FightElo` (strict, like `rankings.json`: `red_pre`, `red_delta`, `blue_pre`, `blue_delta` ints, `red_p4p_rank`,
`blue_p4p_rank` int ≥ 1 or null, all required) and `Fight.elo: FightElo | None = None`. `score` builds
`Timeline.from_pass` from its one `history_pass` and, after the tiers, sets `fight.elo = fight_elo(fight, ev.date,
timeline, shift)` on every fight of every event in data/: `shift` is `display.scale(ratings, now)`'s, the one
`Fighter.elo` uses; only the `rank` comes from `Timeline.p4p(card day)` (its `elo` values are on that day's own
shift). `CONTRACT_VERSION = "1.3"`.

Mutation check: taking the rank from `p4p(day + 1)` (after the card) fails 2 score tests.

### Guarantees
| # | What is guaranteed | Test |
|---|---|---|
| 1 | A completed fight's `elo` = the timeline's `bout(fight_id)` pre + today's shift and own delta, each rounded on its own, and `p4p(card day)`'s ranks; the upset winner's delta > 0 > the loser's | `test_a_completed_fight_gets_each_fighters_elo_going_in_and_its_own_change` |
| 2 | The shift is today's: the winner's pre + delta, decayed to now, + shift is their `Fighter.elo` | `test_the_shift_is_todays_the_same_as_fighter_elo` |
| 3 | The rank is the one going into the card: v5's rank moved with the upset, and the fight shows the pre-card one | `test_the_rank_is_the_one_going_into_the_card_not_after_it` |
| 4 | NC (Overturned) → both deltas 0 | `test_a_no_contest_has_a_zero_change` |
| 5 | Upcoming → null; a completed card in the store under another fight id → null | `test_upcoming_and_unmatched_fights_have_no_elo` |
| 6 | A fight with a result dated after `--now` → null (not in this run's pass) | `test_a_fight_with_a_result_on_an_upcoming_card_has_no_elo_until_the_store_has_it` |
| 7 | `--no-history` leaves `Fight.elo` as it is | `test_no_history_leaves_fight_elo_as_it_is` |
| 8 | Written as a JSON object with the six keys; `null` on upcoming fights | `test_fight_elo_is_written_as_a_json_object` |
| 9 | Published as in data/; optional in `schema.json`, `FightElo` requires all six; contract 1.3 | `test_fight_elo_is_published_and_in_the_schema`, `test_the_contract_version_is_1_3`, `test_index_adds_contract_version_…`, `test_schema_describes_the_top_level_types` |
| 10 | Publish refuses `"1612"`, `2.5` or a rank of 0 | `test_refuses_a_fight_elo_that_is_not_a_strict_int` |
| 11 | CONTRACT.md (1.3 line, field meanings, `score` row), API.md and ELO.md (the day before, the shift caveat, `--as-of`, tournament days) | `tests/test_docs.py` (`…fight_elo…`, `test_contract_covers["1.3"]`) |

### Timing (full store via the `seed_db` fixture, 40 latest cards, 417 fights, three runs)
| Step | Time |
|---|---|
| `history_pass` | 0.06–0.07 s |
| `Timeline.from_pass` + `display.scale` | 0.06–0.08 s |
| `fight_elo` over the 417 fights | 0.59–0.62 s (~15 ms per card: one `p4p(day)` per card date, cached) |

data/ holds a handful of completed cards (7 events in the site's data/ today), so it adds well under 0.2 s per run.

## Review fixes (PR #11: python-reviewer + code-reviewer, triaged; no CRITICAL/HIGH)

### Checkpoints
| Stage | Commit | Command | Result |
|---|---|---|---|
| RED | 7d5dde3 | `uv run pytest -q` | 20 failed (10 timeline: `bout().date`/`.sides`, `ranks`, the copy, the ValueError; 5 score; 3 docs; `test_score_tests_find_the_vendored_model_at_the_repo_root`, which reruns the score suite), 1080 passed, 1 skipped |
| GREEN | 7750bc3 | `uv run pytest -q` | 1100 passed, 1 skipped; `uv run pytest -q -m seed tests/history` 7 passed |

Already green at RED, kept as coverage: the debut test, `FightElo` without a rank key (×2), the `--as-of` test.
Mutation check: taking the rank going into the day after the bout fails 4 score tests, including the F1 one.

### Fixes
| Id | Fix | Test |
|---|---|---|
| F1 | The rank and pre refer to the same day: `Timeline.bout(fight_id)` returns `Bout(date, sides)` with the store's date for the bout, and `fight_elo(fight, timeline, shift)` takes the rank going into `bout.date` (it no longer takes data/'s `ev.date`). The store's day was chosen over null + a warning: pre and delta are already from it, so it is the consistent one. | `test_the_rank_is_taken_going_into_the_stores_day_for_the_bout`, `test_bout_says_the_day_it_was_rated_on` |
| F2 | `Timeline.ranks(day)`: {fighter_id: rank}, cached per day as a `MappingProxyType`; `fight_elo` uses it instead of rebuilding a dict per fight. `p4p(day)` returns a copy (new dicts and lists) of the cached lists. | `test_ranks_are_the_p4p_lists_by_fighter_id_read_only`, `test_changing_the_p4p_lists_a_caller_got_does_not_change_the_next_call` |
| F3 | CONTRACT.md (`Fight.elo`'s pre bullet), API.md and ELO.md: recomputed every run on today's shift, so a completed card's file can change (`ETag`, `Last-Modified`, snapshot) when the shift moves. Not frozen (decided). | `test_contract_says_fight_elo_can_change_a_completed_event_file`, `test_api_doc_says_fight_elo_can_change_a_completed_event_file` |
| F4 | `rating._gender` → `rating.gender_of` (public, docstring); `rate` and the timeline call it. No behaviour change. | the existing `test_gender_from_weight_classes` and equivalence tests |
| F5 | Docstrings on `State`, `Side`, `Bout`, `Timeline`; the module docstring says `score` uses `bout`/`ranks` and `state`/`series` are kept for the Elo chart and historical rankings. A log fight id missing from `bouts` raises `ValueError` naming it. | `test_a_log_row_without_its_bout_is_a_clear_error` |
| F6 | Debut: pre = `round(START + shift)`, rank null. Two bouts on one day: the same pre and rank, each its own delta. Corners swapped between data/ and the store: values follow the fighter ids. The unmatched test now also has a fight id the store has between other fighters (hits the `set(sides) != {red, blue}` guard). `FightElo` without `red_p4p_rank` / `blue_p4p_rank` is refused. | `test_a_fighter_debuting_on_the_card_…`, `test_two_bouts_on_one_day_…`, `test_corners_swapped_…`, `test_upcoming_and_unmatched_fights_have_no_elo`, `test_refuses_a_fight_elo_without_a_rank_key` |
| F7 | Checked: `fightcard history rankings --as-of X` is `history_pass(as_of=X)` (bouts up to and including X) with `p4p(..., X)`, which is exactly `Timeline.p4p(X + 1 day)`. ELO.md now says "up to and including", that `--as-of <D minus one day>` gives the rank going into D, and that `--as-of D` already counts the card. | `test_the_lists_going_into_a_day_are_history_rankings_as_of_the_day_before`, `test_elo_doc_says_as_of_includes_the_day` |

### Checked, not acting
- Per-day P4P cost: 0.6 s for 40 cards / 417 fights, and data/ holds a handful of completed cards; left as is (`ranks` now also saves the per-fight dict).
- Aliasing of `Fight` objects: `score_events` deep-copies every event (`ev.model_copy(deep=True)`), so setting `fight.elo` on what it returns never touches the loaded models.
