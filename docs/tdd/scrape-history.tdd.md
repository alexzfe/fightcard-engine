# TDD evidence: the scrape keeps the history store current; DOB parse fix

- **Source plan:** the orchestrator's Phase 2b brief (`p2b-scrape-history.md`), following open questions 2 and 4 of the Phase 2 report (`docs/tdd/history-rating.tdd.md`).
- **Base:** built on `feat/history-rating` at `a1cb6ba`, then rebased onto `origin/main` at `ff716bc` (PR #2 merged with its review fixes). Those fixes make `score` exit 1 on a missing, unusable or stale store, with `--no-history` to opt out, and make `history ingest` reject a bout with a method but no outcome. The design follows both (see "Rebase onto main").
- Journeys were derived during this run.

## User journeys

1. As a fan, I want a card's results to move the ratings on the refresh that first sees them, without anyone re-running `history ingest`.
2. As the operator, I want a refresh that fails halfway to leave the store as it was, and a re-run on the same pages to change nothing.
3. As the maintainer, I want one parser path: what the scrape puts in the store is what `history ingest` would make of the same pages.
4. As a fan, I want every fighter's date of birth shown, including those whose ufcstats page abbreviates the month.

## Design choice: inside `scrape` (`--history-db`), not a separate `history update`

| | inside `scrape` (chosen) | `history update` after `scrape` |
|---|---|---|
| Parser path | The pages `scrape` already parsed go to the shared `history.ingest` upserts (`upsert_card`, `upsert_fighter_page`, `record_page`), which `history ingest` also uses. | Scrape would have to save the HTML and a fetch log, then the update step would parse it a second time. |
| All-or-nothing | One `with conn:` around the store upserts *and* the data/ write. A fetch error, parse error or store error leaves both unchanged (scrape already fetches and validates everything before writing). | Two commits. If `scrape` succeeds and `update` fails, data/ has the new results but the store doesn't, and `score` then fails on the completed event with no bouts. |
| New surface | Two flags (`--history-db`, `--no-history`), mirroring `score`. | A new subcommand, an on-disk page cache, and its retention. |

- **Missing store:** `scrape` exits 1 before fetching (it never creates a store: a store holding only recent events would rate fighters from a handful of bouts). `--no-history` (exclusive with `--history-db`) scrapes without one. The default path is `score`'s: `<--data>/../history/history.sqlite`, whatever the cwd.
- **A result without an outcome:** a completed bout with a method but no W/L flag raises `history.ingest.MissingOutcome` (the shared `without_outcome` helper `history ingest` also uses) before any upsert. The run exits 1, naming the fights, and nothing is written.
- **Listing:** `COMPLETED_URL` is now `/statistics/events/completed?page=all`, the page `history ingest` reads. It is fetched every run when there's a store, even with `--recent 0`, and recorded under the same `pages` key and path. It's still one request; the full page is about 630 KB.
- **Completed events the store lacks:** completed events in data/ with no bout with a result in the store are refetched (date order) and upserted. This is the precondition the updated `score` checks. After the seed it's usually empty; it covers events data/ already had before the store existed.
- **What goes in:** every event page (upcoming and completed), every fighter page fetched this run, and the listing. They're applied in `history ingest` order: event pages sorted by id, then fighter pages, all stamped with the run's time.
- **Found during GREEN:** a card-name upsert overwrote the name a fighter page had set ("Cris Cyborg" vs "Cristiane Justino"). `ingest` hid it because pages always come after cards there. A scrape re-run whose fighters are fresh fetches no pages, so the card name won and the dump changed. Fix in `store.upsert_fighter`: a card name no longer replaces a name on a row that has a fighter-page record. `test_fighter_upsert_keeps_bio_a_card_name_doesnt_know` now expects the page name.

## Task report

| Step | Command | Result |
|---|---|---|
| Baseline (`a1cb6ba`) | `uv run pytest -q` | 466 passed, 1 skipped, 49 deselected |
| DOB baseline | ingest of the full research cache, then `SELECT COUNT(*) FROM fighters WHERE dob IS NOT NULL` | **357** of 4,628 |
| RED 1 (`c27a047`) | `uv run pytest -q tests/ufcstats/test_parsers.py tests/history/test_history_store.py`; `-m seed tests/history/test_history_seed.py` | `3 failed, 71 passed`: `parse_date("Jul 09, 1985")` / `(" Sep 3, 1990 ")` returned `None`; store DOBs `{'634bb0de2eb043b4': None} != {... '1985-07-09'}`. Seed: `1 failed, 3 passed` (DOBs vs research `fighter_data.csv`) |
| GREEN 1 (`7fd16e6`) | `uv run pytest -q`; `-m seed` | `472 passed, 1 skipped, 50 deselected`; seed `5 passed` |
| DOB after | same ingest | **4,124** of 4,628 (+3,767). Every one equals research's `fighter_dob`, and all 4,628 equal `model/fighter_snapshot.parquet` `dob` |
| RED 2 (`91a6f7e`) | `uv run pytest -q tests/ufcstats` | `15 failed, 120 passed`: 10 × `TypeError: scrape() got an unexpected keyword argument 'history'`, CLI tests `SystemExit: 2` (unknown `--no-history`), 1 × `AttributeError: 'Namespace' object has no attribute 'history_db'` |
| GREEN 2 (`27294a9`) | `uv run pytest -q`; `-m seed`; `-m docker` | `488 passed, 1 skipped, 50 deselected`; seed `5 passed`; docker `44 passed` |
| Rebase onto `origin/main` (`ff716bc`) | `git fetch origin && git rebase origin/main` | Two conflicts. (1) `history/ingest.py`: main's `MissingOutcome` check sat in the loop this branch had moved into `upsert_card`; kept it as `no_outcome += without_outcome(parsed)`, a new shared helper. (2) `docs/CONTRACT.md`: kept main's `history ingest` / `score` bullets and added the `scrape` bullet. After the rebase, `uv run pytest -q` gave `1 failed, 504 passed`: `test_cli_history_db_defaults_to_score_s_default`, because score's default is now `None` (resolved from `--data`) |
| RED 3 (`72f1ebc`) | `uv run pytest -q tests/ufcstats/test_scrape_history.py` | `4 failed, 14 passed`: default `history/history.sqlite` ≠ `None`; `DID NOT RAISE SystemExit` (flags not exclusive); default store beside data/ not found from another cwd (exit 1); a bout with a method but no outcome exited 0 |
| GREEN 3 (`6efd8ca`) | `uv run pytest -q`; `-m seed`; `-m docker` | `508 passed, 1 skipped, 51 deselected`; seed `6 passed`; docker `44 passed` |
| Coverage | `uv run pytest -q --cov=fightcard --cov-report=term-missing` | `history/ingest.py` 100%, `history/store.py` 100%, `ufcstats/stage.py` 99% (lines 59, 197: `utcnow`, and the all-upcoming-empty branch, both uncovered before this work), `ufcstats/parsers.py` 98%. Total 99% |

## Test specification

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| 1 | "Jul 09, 1985" and "May 21, 1989" both parse; "--" and "" give `None` | `tests/ufcstats/test_parsers.py::test_parse_date_reads_full_and_abbreviated_month_names` | unit | PASS |
| 2 | Fighter-page DOBs reach the store | `tests/history/test_history_store.py::test_fighter_page_dobs_with_abbreviated_months_are_kept` | integration | PASS |
| 3 | Seeded DOBs equal research's `fighter_data.csv` for every fighter (> 4,000 non-null) | `tests/history/test_history_seed.py::test_fighter_dobs_agree_with_research_fighter_data` | seed | PASS |
| 4 | A card going upcoming → completed updates the store (status, outcome, winner, method), and `score` then counts the new bout for every winner (n_bouts + 1, last bout = card date, Elo set and changed), with no re-ingest | `tests/ufcstats/test_scrape_history.py::test_a_card_going_upcoming_to_completed_updates_the_store_and_score_rates_the_winners` | integration | PASS |
| 5 | Re-running on the same pages leaves the dump byte-identical | `…::test_rerunning_on_the_same_pages_changes_nothing` | integration | PASS |
| 6 | An event page failing mid-run leaves the store and data/ unchanged, exit 1 | `…::test_an_event_page_failing_mid_run_leaves_the_store_as_it_was` | integration | PASS |
| 7 | A store write failing on the 5th upsert rolls back every earlier upsert and writes no data/, exit 1, "nothing written" | `…::test_a_store_write_failing_mid_run_rolls_back_and_writes_no_data` | integration | PASS |
| 8 | Single parser path: the store after a scrape equals `history ingest` of the same pages (events, fights exactly; fighters and pages except `fetched_at`) | `…::test_the_store_is_what_history_ingest_makes_of_the_same_pages` | integration | PASS |
| 9 | Fetched pages and fighter rows carry the run's time | `…::test_fetched_pages_are_recorded_with_this_run_s_time` | integration | PASS |
| 10 | The full listing (`?page=all`) is fetched and recorded even with `--recent 0`; without a store `--recent 0` still skips it | `…::test_the_full_completed_listing_is_refreshed_even_with_recent_zero`, `…::test_without_a_store_recent_zero_still_skips_the_listing` | integration | PASS |
| 11 | A listed event is `is_ufc = 1`, `source = listing` | `…::test_a_listed_event_is_marked_ufc_from_the_listing` | integration | PASS |
| 12 | A completed event in data/ that the store lacks is fetched into it; one it has is not refetched | `…::test_a_completed_event_in_data_the_store_lacks_is_fetched_into_it`, `…::test_a_completed_event_the_store_already_has_is_not_refetched` | integration | PASS |
| 13 | CLI: `--history-db` updates that store; a missing store exits 1 before any request and creates nothing; `--no-history` scrapes without one and is exclusive with `--history-db`; the default store is `<--data>/../history/history.sqlite` from any cwd, as for `score` | `…::test_cli_*` (6 tests) | integration | PASS |
| 15 | A completed bout with a method but no W/L outcome fails the run (exit 1), names the fight, and writes neither the store nor data/ | `…::test_a_completed_bout_with_a_method_but_no_outcome_fails_and_writes_nothing` | integration | PASS |
| 14 | A card name fills in until a fighter page names the fighter, then the page name stays | `tests/history/test_history_store.py::test_a_card_name_fills_in_until_a_fighter_page_names_the_fighter` | unit | PASS (written in GREEN after #5 exposed the bug; see Design) |

## Age features

`score`'s age features read `dob` from `model/fighter_snapshot.parquet` (`model/feature_spec.json`: `"kind": "age", "column": "dob"`), not from `fighters.json` or the store. The snapshot already has 4,124 DOBs, equal to the fixed store's for all 4,628 fighters, so tiers don't change. Only `Fighter.dob` on the site and `fighters.dob` in the store gain values.

## Coverage and known gaps

- The scrape records each page's sha256 but doesn't keep the HTML. Rebuilding the store offline after a parser fix needs the seed cache plus a re-fetch of what the scrape added (ELO.md "Future").
- Existing tests changed on purpose: `test_scrape_stage.py::test_run_builds_client_and_scrapes` and `::test_cli_dispatches_scrape` now pass `--no-history` / `no_history=True`. `test_history_store.py::test_fighter_upsert_keeps_bio_a_card_name_doesnt_know` now expects the page name.

## Rebase onto main

The branch was rebased onto `origin/main` at `ff716bc`, after PR #2 merged. The RED/GREEN SHAs above are the rebased ones; the commit messages of the first four still quote the pre-rebase SHAs (`fbea58d`, `c0ceb72`, `cb94ea2`, `072ee6b`) and the pre-rebase counts. Final counts are in GREEN 3's row.

## Review fixes (PR #3)

Findings from an ecc code review, fixed with one RED → GREEN pair per finding or tight group. Principle: data/
ahead of the store is the dangerous direction (`score`'s staleness check fails on data/ events the store lacks);
a store ahead of data/ is harmless, since the next run rewrites data/.

| Step | Command | Result |
|---|---|---|
| RED 1 (`fe84849`) | `uv run pytest -q tests/ufcstats/test_scrape_history.py` | `5 failed, 18 passed`: data/ written before a failed COMMIT; no `BUSY_TIMEOUT_MS`; `OSError: [Errno 28]` escaped `scrape()` (traceback) with and without a store |
| GREEN 1 (`b3f8ea3`) | `uv run pytest -q` | `513 passed, 1 skipped`. `write()`: `PRAGMA busy_timeout`, `BEGIN IMMEDIATE`, store upserts, commit (rollback on any error), then data/; `OSError` there → `DataWriteFailed`, exit 1, "store updated, data/ may be partially written; the next run rewrites it" |
| RED 2 (`b09b5ae`) | same file | `3 failed, 25 passed`: exit 1 on a backfill 404 and on a backfill page without outcomes; no `BACKFILL_PER_RUN` |
| RED 2b (`ef8a5f9`) | same file | `1 failed, 28 passed`: a backfilled card refetched its stale known fighters. Run on top of the uncommitted backfill split in stage.py (test-only commit) |
| GREEN 2 (`c557ba2`) | `uv run pytest -q` | `519 passed, 1 skipped`. `results()` fetches this run's cards loudly, then `backfill()`: oldest first, ≤ 20, 404 / `MissingOutcome` → WARNING + `_forget` (no page, no card, data/ file kept); backfilled cards fetch only fighters missing from fighters.json |
| RED 6 (`7b4c454`) | same file | `1 failed, 29 passed`: a 0-fight upcoming event's `events` row was upserted |
| GREEN 6 (`5bb9dc5`) | `uv run pytest -q` | `520 passed, 1 skipped`. The skip calls `_forget` |
| Refactor 5 (`5005168`) | `uv run pytest -q` | `520 passed, 1 skipped`. The `url.endswith(tuple(self.fighter_pages))` filter is gone: every page left in `self.pages` is recorded |
| Docs 7, 8 (`58d13d7`) | — | CONTRACT.md: `?page=all` (also with `--no-history` when `--recent` > 0), store-first commit, backfill cap and skip, 0-fight events, the accepted fresh-fighter gap. ELO.md in brief |
| Coverage test (`41807e5`) | `uv run pytest -q tests/ufcstats` | `151 passed`. A backfill 503 still fails the run (passes on the GREEN code; not a RED) |
| Final | `uv run pytest -q`; `-m seed`; `-m docker` | `521 passed, 1 skipped, 51 deselected`; seed `6 passed`; docker `44 passed` |
| Coverage | `uv run pytest -q --cov=fightcard --cov-report=term-missing` | `ufcstats/stage.py` 99% (72 `utcnow`, 212 all-upcoming-empty, both uncovered before), `history/ingest.py` 100%, `history/store.py` 100%, `score/stage.py` 99%. Total 99% |

| # | What is guaranteed | Test (`tests/ufcstats/test_scrape_history.py`) |
|---|---|---|
| R1 | A failed COMMIT leaves data/ untouched and the store as it was; exit 1, "nothing written" | `test_a_store_commit_failing_leaves_data_untouched` |
| R2 | Another writer holding the store: the run waits `busy_timeout`, fails before writing anything, says "locked"; the default wait is 30 s | `test_the_store_write_lock_is_taken_with_a_busy_timeout`, `test_the_default_busy_timeout_is_thirty_seconds` |
| R3 | `OSError` writing data/ after the commit: exit 1 with the true message, store committed; the next clean run gives the same store dump and data/ tree as a clean run from scratch | `test_a_data_write_failing_after_the_commit_says_so_and_the_next_run_converges` |
| R4 | Without a store, the same `OSError` exits 1 with the message, no traceback | `test_without_a_store_a_data_write_failing_exits_1_without_a_traceback` |
| R5 | Backfill page 404 / without outcomes: skipped, WARNING names the event (and the fights), no store rows or page, data/ file unchanged, the rest goes in, exit 0 | `test_a_backfill_page_that_404s_is_skipped_with_a_warning`, `test_a_backfill_page_without_outcomes_is_skipped_with_a_warning` |
| R6 | This run's cards still fail loudly: the `--recent` card 404 (with a backfill pending), a past-upcoming card without outcomes, a backfill non-404 HTTP error | `test_a_current_card_that_404s_still_fails_the_run`, `test_a_past_upcoming_card_without_outcomes_still_fails_the_run`, `test_a_backfill_page_failing_with_another_http_error_still_fails_the_run` |
| R7 | 25 events to backfill: 20 this run (oldest first, INFO says 5 wait), the other 5 next run | `test_backfill_refetches_are_capped_per_run_and_the_rest_come_next_run` |
| R8 | A backfilled card fetches only fighters missing from fighters.json | `test_backfill_cards_fetch_only_fighters_missing_from_data` |
| R9 | A 0-fight upcoming event: no data/ file, no store event or page row | `test_an_upcoming_event_skipped_for_0_fights_stays_out_of_the_store_as_out_of_data` |

### Finding 3 (refetch loop for empty completed cards): not done, needs a decision

The decided fix ("a store event row with status `completed` satisfies the check") can't take effect: the parser
sets `status = "completed" if any(f.result for f in fights) else "upcoming"` (`ufcstats/parsers.py:201`), so a card
with no bouts is always parsed, and stored, as `upcoming`. The backfill also rewrites the data/ file from that
parse as `upcoming`, which then puts it in the past-upcoming path (refetched every run as a current card). The
RED tests written for it (scrape: backfilled once; score: an empty completed store row passes) are kept outside
the repo. Choosing where "completed" comes from for an empty card (the completed listing, data/'s status, or
the date) changes data/ (published as `/v1/`) and `history ingest`, so it's left for a decision.
