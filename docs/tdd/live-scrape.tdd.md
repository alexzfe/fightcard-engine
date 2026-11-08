# TDD evidence: fight-night live refresh (5-minute scrapes of the live event)

Source: the orchestrator's live-fight-night Phase 2 brief (engine: 5-minute scrapes of the live event on fight days).
Evidence from fight night 2026-11-07 (UFC Fight Night: Cortes Acosta vs. Walker, `110404b505fb562c`): the engine
only refreshed hourly, so results reached `/v1/` up to an hour after ufcstats posted them. Branch
`feat/live-scrape` from `origin/main` at 2d44b18.

## User journeys

1. As a fan on fight night, I want a bout's result on `/v1/` within about 5 minutes of ufcstats posting it, not at
   the next hourly slot.
2. As the site owner, I want that without more load on ufcstats than needed: only the live card's page, only while
   it isn't fully resulted, never from staging, and backed off when ufcstats fails.
3. As a client of `/v1/`, I want `generated_at` to move exactly when the data did.
4. As the site owner, I want a bout that has a method but no W/L flag yet to wait, not fail every run.
5. As the site owner, I want to know, next fight night, how fast ufcstats posts results (a log line per bout).

## Task report

| Deliverable | RED | GREEN | Command | RED evidence | GREEN evidence |
|---|---|---|---|---|---|
| `scrape --live`, `service.live`, runner `--event live`, serve fast loop + backoff, held outcome-less bouts, `result_seen`, fighter refresh | 4c164a5 | 3d89fc3 | `uv run pytest` | 1128 passed, 1 skipped, 1 failed (`test_layout`: the new suites don't collect), 2 collection errors: `cannot import name 'live' from 'fightcard.service'` | 1196 passed, 1 skipped |
| Docs: SERVICE.md "Live refresh", the timeout row; CONTRACT.md `generated_at` | 878d975 | 5774ede | `uv run pytest tests/test_docs.py` | 12 failed, 175 passed | 187 passed; full suite 1208 passed, 1 skipped (the commit message says 1199: a typo) |
| Error-path tests (unreadable index, store rollback, a data write failing after the commit, the live gate failing) and a help-text wrap | | the next commit | `uv run pytest` | | 1212 passed, 1 skipped |

Two test fixes between RED and GREEN, both in the tests' own assumptions: the completed fixture card has 12 bouts,
not 13, and the "no results yet" test now seeds the page's own bouts (a card whose bouts changed is a change).

`uv run pytest -m docker` (the container tests): 56 passed.

## Test specification

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| 1 | A live scrape fetches only the live event's page: no listings, fighter pages or other cards | `tests/ufcstats/test_scrape_live.py::test_it_fetches_the_live_event_s_page_and_nothing_else` | unit | PASS |
| 2 | New results are written, the card moves to `recent`, `generated_at` moves | `test_new_results_are_written_and_generated_at_moves`, `test_a_card_with_results_moves_to_recent` | unit | PASS |
| 3 | Nothing new: exit 3 (`live.UNCHANGED`), data/ and the store byte-identical | `test_nothing_new_writes_nothing_and_says_unchanged`, `test_a_card_without_results_yet_is_unchanged`, `test_unchanged_leaves_the_history_store_alone` | unit | PASS |
| 4 | Live window: indexed, today or yesterday (UTC), not fully resulted (a method without an outcome isn't a result), has bouts | `test_due_events[8]`, `test_due_events_only_looks_at_indexed_events`, `test_two_days_after_it_stops`, `test_a_fully_resulted_card_is_not_fetched_again` | unit | PASS |
| 5 | The live window sits inside `RESULTS_GRACE`; `service.live` is stdlib-only (PID 1 imports it) | `test_the_live_window_sits_inside_the_results_grace`, `test_live_is_stdlib_only` | unit | PASS |
| 6 | Tiers, `tier_frozen_at`, segments kept; fighters already in data/ untouched; a new fighter gets a stub so publish builds | `test_tiers_and_segments_are_kept`, `test_fighters_already_in_data_are_left_as_they_are`, `test_a_new_fighter_on_the_card_gets_a_stub_so_publish_can_build` | unit | PASS |
| 7 | A bout with a method but no W/L outcome is held (warning), not fatal, live and in the hourly run within `RESULTS_GRACE`; older cards still fail as before | `test_a_bout_with_a_method_but_no_outcome_is_held_back_not_fatal`, `test_a_held_bout_comes_in_once_its_outcome_is_posted`, `test_the_hourly_run_holds_an_outcome_less_bout_on_tonight_s_card_instead_of_failing`; existing `test_scrape_history.py::test_a_completed_bout_with_a_method_but_no_outcome_fails_and_writes_nothing` | unit | PASS |
| 8 | `result_seen event= fight= seen_at=` once per bout, from the live or the hourly scrape | `test_each_result_s_first_sighting_is_logged_once`, `test_the_hourly_scrape_logs_first_sightings_too` | unit | PASS |
| 9 | Fetch errors (HTTP, PoW halt), an unreadable index, a store write failing: exit 1, nothing written; a data write failing after the commit says so | `test_a_fetch_error_fails_and_writes_nothing[2]`, `test_an_unreadable_index_fails_and_fetches_nothing`, `test_a_store_write_failing_rolls_back_and_writes_no_data`, `test_a_data_write_failing_after_the_store_says_so` | unit | PASS |
| 10 | The store gets the results, keeping the event's `source` / `is_ufc` | `test_results_go_into_the_history_store_too` | integration (SQLite) | PASS |
| 11 | The hourly run refreshes the fighters whose bout the live loop completed, and only theirs | `test_the_hourly_run_refreshes_fighters_whose_bout_the_live_loop_already_completed` | unit | PASS |
| 12 | `FIGHTCARD_LIVE_MINUTES`: default 5, `off`/`0` off, 1-60, never where fetching is off (staging) | `tests/service/test_live_loop.py::test_the_live_interval_comes_from_the_environment[8]`, `test_a_bad_live_interval_is_refused[4]` | unit | PASS |
| 13 | `run --event live` = gate → scrape --live (5 min timeout) → score → publish; exit 3 ends it `status=unchanged`, exit 0; no live event or fetch off runs nothing; exit 3 still fails a full run | `test_a_live_run_is_scrape_live_score_publish`, `test_nothing_new_skips_score_and_publish`, `test_unchanged_means_nothing_to_the_hourly_run`, `test_with_no_live_event…`, `test_where_fetching_is_off…`, `test_a_live_run_s_scrape_has_a_short_timeout`, `test_an_unreadable_index_fails_the_live_gate` | unit | PASS |
| 14 | serve: a live run every 5 min on fight night, the hourly slot unchanged; none off fight days, once all results are in, or with the loop off; a slot during another run is skipped, not queued; 15 min backoff after a failed run; unreadable data is logged and serve keeps going | `test_on_fight_night_a_live_run_starts_every_five_minutes`, `test_off_fight_days…`, `test_once_every_result_is_in…`, `test_the_loop_stops_when_a_run_brings_the_last_result_in`, `test_with_the_loop_off…`, `test_a_live_slot_during_another_run_is_skipped_not_queued`, `test_a_failed_run_backs_the_loop_off`, `test_unreadable_data_does_not_stop_serve` | unit (fake clock/procs) | PASS |
| 15 | Docs cover the live refresh, its timings and `generated_at` | `tests/test_docs.py::test_service_doc_covers_the_live_refresh[10]`, `test_service_doc_states_the_live_timings`, `test_contract_says_when_generated_at_moves` | doc | PASS |

## Measurement: is a full score cheap enough every 5 minutes?

On a store built with `fightcard history ingest` from the research cache (1,288 events, 11,694 fights, 4,628 fighters)
and the site's data/ (desktop, 22 cores): `history_pass` 0.08 s, `Form.from_store` 0.09 s, `fightcard score` 1.2 s
wall (warm), `fightcard publish` 0.8 s. So the live run reruns score and publish whole rather than patching the
changed fights; on the VPS's 2 vCPUs at nice 10, expect a few seconds.

## Coverage and gaps

`uv run pytest tests/service tests/ufcstats --cov=fightcard.service --cov=fightcard.ufcstats.stage` (before the
error-path tests): `service/live.py` 100%, `runner.py` 95%, `serve.py` 94%, `ufcstats/stage.py` 97%. The error-path
tests in the last commit cover the live gate's failure, the store rollback and the data-write failure.

Gaps: no real ufcstats fetch was made (the scrapers are fixture-tested; `-m live` was not run). How fast ufcstats
actually posts results is what the `result_seen` lines will show on the next fight night.
