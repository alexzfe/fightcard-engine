# TDD evidence: the scrape keeps the history store current; DOB parse fix

- **Source plan:** the orchestrator's Phase 2b brief (`p2b-scrape-history.md`), following open questions 2 and 4 of the Phase 2 report (`docs/tdd/history-rating.tdd.md`).
- **Base:** `feat/history-rating` at `a1cb6ba`. The orchestrator's note that the PR #2 fixes make `score` exit non-zero without a usable store (opt out with `--no-history`), and fail when a completed event in data/ has no bouts in the store, is reflected in the design below.
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

- **Missing store:** `scrape` exits 1 before fetching (it never creates a store: a store holding only recent events would rate fighters from a handful of bouts). `--no-history` scrapes without one. The default path `history/history.sqlite` is `score`'s.
- **Listing:** `COMPLETED_URL` is now `/statistics/events/completed?page=all`, the page `history ingest` reads. It is fetched every run when there's a store, even with `--recent 0`, and recorded under the same `pages` key and path. It's still one request; the full page is about 630 KB.
- **Completed events the store lacks:** completed events in data/ with no bout with a result in the store are refetched (date order) and upserted. This is the precondition the updated `score` checks. After the seed it's usually empty; it covers events data/ already had before the store existed.
- **What goes in:** every event page (upcoming and completed), every fighter page fetched this run, and the listing. They're applied in `history ingest` order: event pages sorted by id, then fighter pages, all stamped with the run's time.
- **Found during GREEN:** a card-name upsert overwrote the name a fighter page had set ("Cris Cyborg" vs "Cristiane Justino"). `ingest` hid it because pages always come after cards there. A scrape re-run whose fighters are fresh fetches no pages, so the card name won and the dump changed. Fix in `store.upsert_fighter`: a card name no longer replaces a name on a row that has a fighter-page record. `test_fighter_upsert_keeps_bio_a_card_name_doesnt_know` now expects the page name.

## Task report

| Step | Command | Result |
|---|---|---|
| Baseline (`a1cb6ba`) | `uv run pytest -q` | 466 passed, 1 skipped, 49 deselected |
| DOB baseline | ingest of the full research cache, then `SELECT COUNT(*) FROM fighters WHERE dob IS NOT NULL` | **357** of 4,628 |
| RED 1 (`fbea58d`) | `uv run pytest -q tests/ufcstats/test_parsers.py tests/history/test_history_store.py`; `-m seed tests/history/test_history_seed.py` | `3 failed, 71 passed`: `parse_date("Jul 09, 1985")` / `(" Sep 3, 1990 ")` returned `None`; store DOBs `{'634bb0de2eb043b4': None} != {... '1985-07-09'}`. Seed: `1 failed, 3 passed` (DOBs vs research `fighter_data.csv`) |
| GREEN 1 (`c0ceb72`) | `uv run pytest -q`; `-m seed` | `472 passed, 1 skipped, 50 deselected`; seed `5 passed` |
| DOB after | same ingest | **4,124** of 4,628 (+3,767). Every one equals research's `fighter_dob`, and all 4,628 equal `model/fighter_snapshot.parquet` `dob` |
| RED 2 (`cb94ea2`) | `uv run pytest -q tests/ufcstats` | `15 failed, 120 passed`: 10 × `TypeError: scrape() got an unexpected keyword argument 'history'`, CLI tests `SystemExit: 2` (unknown `--no-history`), 1 × `AttributeError: 'Namespace' object has no attribute 'history_db'` |
| GREEN 2 (`072ee6b`) | `uv run pytest -q`; `-m seed`; `-m docker` | `488 passed, 1 skipped, 50 deselected`; seed `5 passed`; docker `44 passed` |
| Coverage | `uv run pytest -q --cov=fightcard --cov-report=term-missing` | `history/ingest.py` 100%, `history/store.py` 100%, `ufcstats/stage.py` 99% (lines 55, 191: `utcnow`, and the all-upcoming-empty branch, both uncovered before this work), `ufcstats/parsers.py` 98%. Total 99% |

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
| 13 | CLI: `--history-db` updates that store; a missing store exits 1 before any request and creates nothing; `--no-history` scrapes without one; the default path is `score`'s | `…::test_cli_*` (4 tests) | integration | PASS |
| 14 | A card name fills in until a fighter page names the fighter, then the page name stays | `tests/history/test_history_store.py::test_a_card_name_fills_in_until_a_fighter_page_names_the_fighter` | unit | PASS (written in GREEN after #5 exposed the bug; see Design) |

## Age features

`score`'s age features read `dob` from `model/fighter_snapshot.parquet` (`model/feature_spec.json`: `"kind": "age", "column": "dob"`), not from `fighters.json` or the store. The snapshot already has 4,124 DOBs, equal to the fixed store's for all 4,628 fighters, so tiers don't change. Only `Fighter.dob` on the site and `fighters.dob` in the store gain values.

## Coverage and known gaps

- The scrape records each page's sha256 but doesn't keep the HTML. Rebuilding the store offline after a parser fix needs the seed cache plus a re-fetch of what the scrape added (ELO.md "Future").
- Existing tests changed on purpose: `test_scrape_stage.py::test_run_builds_client_and_scrapes` and `::test_cli_dispatches_scrape` now pass `--no-history` / `no_history=True`. `test_history_store.py::test_fighter_upsert_keeps_bio_a_card_name_doesnt_know` now expects the page name.
