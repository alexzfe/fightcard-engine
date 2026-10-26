# TDD evidence: bout-history store and the single full-history rating

- **Source plan:** the orchestrator's Phase 2 brief (`p2-history-rating.md`) and the Phase 0 recon (§1 seed, §2 rating, §5 store format).
- **Replaces:** the interim rating split, where research exported `model/fighter_rating.parquet` and `score` rolled it forward over at most 3 `recent_fights` (`docs/tdd/elo-pipeline.tdd.md`). The finish model and its snapshot path are unchanged.
- Journeys were derived during this run.

## User journeys

1. As the rating, I need to tell a draw (moves ratings) from a no contest (doesn't), so parsed results carry an explicit outcome.
2. As the operator, I want one SQLite file holding every ufcstats event, bout and fighter, with the raw page each row came from. Re-ingesting is idempotent, a failed run changes nothing, and a sorted dump diffs cleanly.
3. As the operator, I want to seed that store from research's cached HTML with this repo's own parsers, and know it agrees with research's CSVs bout for bout.
4. As a fan, I want every fighter rated from their whole history, so nobody goes `null` because they fought more than 3 times since the last export.
5. As the maintainer, I want one rating implementation with its golden cases generated in this repo, and parity with research's last export as proof.

## Layout

| Module | Role |
|---|---|
| `models.py` | `Result.outcome: "win" \| "draw" \| "nc" \| None` (optional, additive). |
| `ufcstats/parsers.py` | `_parse_result` sets `outcome` from the W/L flag (`loss` → `win` for blue). `winner` is unchanged. |
| `history/store.py` | Schema (`events`, `fights`, `fighters`, `pages`; FK fights→events, CHECKs on outcome/status/source), keyed upserts (`upsert_event` also removes bouts that left the card), `dump` (sorted JSONL), `is_ufc_event`, `rating_bouts` (research's bout-record shape, scope = UFC or ≥4 bouts with a result, ≤ `as_of`), `names`. |
| `history/ingest.py` | One transaction: listing → `is_ufc`/`source`, event pages → events/fights/card names, fighter pages → bio/record/name, `_fetch_log.jsonl` → `pages.url/fetched_at`. |
| `history/stage.py` | `fightcard history ingest --html-dir --db [--dump]`; exits 1 without the completed listing, before touching the store. |
| `history/rating.py` | Research `main:export/rating.py` ported: `rate`, `k_factor`, `expected`, `decayed`, `result_score`, `_gender`, `records`, `CASES`/`golden_cases`, `PARAMS`, plus `history_ratings(conn, as_of)`. `python -m fightcard.history.rating <path>` writes the golden cases. |
| `history/display.py` | Moved from `score/elo.py`: decay to now, anchor, P4P 15/10, tags, best win on the display scale. Constants come from `PARAMS["display"]`. |
| `score/stage.py` | `--history-db` (default `history/history.sqlite`, opened read-only). Bouts up to `--now`. No store means everyone is unrated and tiers still score. |
| `score/bundle.py` | No longer loads `fighter_rating.parquet`. |
| `score/elo.py` | **Deleted** (`roll_forward`, `states_from_export`, `_newer_bouts`, the record-mismatch → unrated rule). |

## Task report

| Step | Command | Result |
|---|---|---|
| Baseline (`c1efe63`, before the rebase onto `5c0eabe`) | `uv run pytest -q` | 357 tests (356 passed, 1 skipped), 1 deselected |
| RED 1 (`5716a0d`) | `uv run pytest -q tests/ufcstats/test_parsers.py` | `6 failed, 32 passed`, all `AttributeError: 'Result' object has no attribute 'outcome'` |
| GREEN 1 (`db0fc19`) | `uv run pytest -q` | `362 passed, 1 skipped, 1 deselected` |
| RED 2 (`c7939ba`) | `uv run pytest -q` | `13 failed, 361 passed, 21 errors`: `NotImplementedError` from the `history` stubs (fixture `connect`, `is_ufc_event`), and `history` not yet a CLI stage. `-m seed`: 3 errors (same stub) |
| GREEN 2 (`a0da475`) | `uv run pytest -q`; `uv run pytest -q -m seed tests/history` | `395 passed, 1 skipped, 4 deselected`; seed `3 passed` (25.5 s) |
| RED 3 (`e0e08e9`) | `uv run pytest -q tests/history` | `60 failed, 33 passed`: 59 × `NotImplementedError` (rating/display stubs), 1 × `assert 1 == None` (`FORMAT_VERSION` stub). Seed parity: 1 failed |
| GREEN 3 (`bd7f719`) | `uv run pytest -q`; `-m seed` | `455 passed, 1 skipped, 5 deselected`; seed `4 passed` |
| RED 4 (`90dd8d6`) | `uv run pytest -q` | `7 failed, 381 passed`: 6 score-stage tests with `SystemExit: 2` (unknown `--history-db`), plus `test_layout.py::test_score_tests_find_the_vendored_model_at_the_repo_root`, which runs the same file |
| GREEN 4 (`123db51`) | `uv run pytest -q`; `uv run pytest -q -m seed` | `388 passed, 1 skipped, 5 deselected`; seed `4 passed, 390 deselected` (26 s) |
| Coverage | `uv run pytest -q --cov=fightcard` | `history/` store 100%, stage 100%, display 100%, ingest 98% (a blank fetch-log line), rating 98% (the `__main__` block, which runs in a subprocess test). `score/stage.py` 99%. Total 99% |
| Seed ingest | `fightcard history ingest --html-dir ~/Projects/fight-auto-research/data/raw_html/ufcstats --db <scratch>` | `1288 events, 11694 fights, 4628 fighters, 5917 pages`, about 25 s |

## Seed cross-check (`-m seed`, full research cache)

- **Bouts:** all 11,600 rows of research's `fight_data.csv` are in the store, on the same event, with the same two fighters, the same winner (an id, `Draw` or `NC`) and the same method. 0 mismatches. Research `(result, result_details)` maps to the event page's method like this: Decision Unanimous/Split/Majority/Decision → `U-DEC`/`S-DEC`/`M-DEC`/`Decision`; `TKO - Doctor's Stoppage` → `KO/TKO`; `Could Not Continue` → `CNC`. ufcstats lists the winner in the left corner, so 3,646 bouts have red/blue swapped against research's fighter_1/fighter_2. The rating's `fighter_a` is the lower id either way.
- **Extra bouts:** up to research's `as_of` (2026-10-14), the store has exactly 14 completed bouts research lacks, all on old PRIDE / Strikeforce cards. ufcscraper builds rows from fight-details pages, and those pages aren't in the cache. After `as_of` there are 13 more bouts (UFC Fight Night 2026-10-17), from pages fetched on 2026-10-26.
- **UFC events:** the rule "in the completed listing, or named `^UFC\b(?! BJJ)`" agrees with `event_source.csv` on `is_ufc` **and** `source` for all 1,280 events. The old site prefix rule (not `DWCS` / `Road to UFC`) disagrees on 368 history events (PRIDE, Strikeforce, WEC, …). The regex alone misses 32 listing events (TUF finales, Noche UFC, Ortiz vs Shamrock 3). **Chosen:** research's rule, so the parity holds. The listing is required for ingest.

## Parity with `model/fighter_rating.parquet`

`rate(rating_bouts(store, as_of="2026-10-14") ∩ research's fight_ids)` vs the vendored file:

- 4,146 = 4,146 fighters, same ids in the same order.
- `rating`, `peak` and `best_win_rating` have max abs diff **0.0** (≤ 1e-9 asserted) and no null mismatches.
- `last_bout_date`, `n_bouts`, `n_ufc_bouts`, `gender`, `best_win_opponent_id` and `best_win_date` match exactly.
- `best_win_opponent` differs in 1 row (`1971869b7e054261`). Its best win is over "Tiago dos Santos e Silva", the fighter-page name; research's `fighter_data.csv` splits it to "Tiago".

With the 14 extra bouts included, the result is what `score` now uses: 2,638 fighters' raw ratings differ by more than 1e-9 (max abs 78.3; the most-affected fighter gains 4 bouts, 2 → 6). On the display scale at 2026-10-26, 6 of 771 active fighters move by 1 point, and the P4P lists are identical.

## Test specification

| # | Guarantee | Test | Type | Result |
|---|---|---|---|---|
| 1 | Parsed results tell win (either corner), draw and NC apart; no flag → `None`; older data without the field validates | `tests/ufcstats/test_parsers.py::test_result_outcome_tells_a_draw_from_a_no_contest[*]`, `test_result_without_a_flag_has_no_outcome`, `test_result_outcome_is_optional_so_older_data_still_validates` | unit | PASS |
| 2 | UFC event = listed or `^UFC\b(?! BJJ)` (TUF, Noche, UFC 1 yes; UFC BJJ, UFCW, DWCS, Road To UFC, PRIDE no) | `tests/history/test_history_store.py::test_ufc_event_is_listed_or_named_ufc[*]` | unit | PASS |
| 3 | Schema: four tables, FK fights→events, outcome CHECK | `test_connect_creates_the_tables`, `test_fights_need_their_event`, `test_outcome_is_constrained` | unit | PASS |
| 4 | Keyed upserts: a corrected result replaces the row; bouts that leave a card are removed; a card name never wipes fighter-page bio | `test_upsert_event_replaces_fields_and_card_by_key`, `test_fighter_upsert_keeps_bio_a_card_name_doesnt_know` | unit | PASS |
| 5 | Ingest loads every fixture page: events, results, `is_ufc`/`source`, draws vs NC, fighter bio, page url/sha256/fetch time (null without a log line) | `test_ingest_*`, `test_fighter_pages_fill_bio_and_name`, `test_pages_record_url_path_hash_and_fetch_time` | integration | PASS |
| 6 | Re-ingest is idempotent (byte-identical dump); the dump is sorted JSON lines | `test_reingest_is_idempotent`, `test_dump_is_sorted_json_lines` | integration | PASS |
| 7 | One transaction: a page failing mid-run leaves the store as it was | `test_a_failing_ingest_leaves_the_store_unchanged` | integration | PASS |
| 8 | No completed listing → refuse; the stage exits 1 without creating the db | `test_ingest_without_the_completed_listing_refuses`, `test_history_stage.py::test_missing_html_dir_or_listing_fails_without_touching_the_store` | integration | PASS |
| 9 | Rating bout records: scope (UFC, or ≥4 bouts with a result), date order, fighter_a = lower id, result from a's side, `ufc` flag, ≤ `as_of` | `test_rating_bouts_*`, `test_a_non_ufc_card_needs_four_bouts_with_a_result` | unit | PASS |
| 10 | CI cross-check on the committed fixture: every research bout and `is_ufc` matches | `test_ingest_matches_research_on_every_fixture_bout`, `test_ingest_agrees_with_research_is_ufc_on_every_fixture_event` | integration | PASS |
| 11 | Seed: 11,600/11,600 bouts match; only the 14 known extras; 1,280/1,280 `is_ufc` + `source` | `test_history_seed.py` (`-m seed`) | parity | PASS |
| 12 | Formula pieces: K, E, W by result/method (incl. `Decision`), unknown result raises, decay | `test_history_rating.py::test_k_*`, `test_expected_score`, `test_result_score_*`, `test_decay_*` | unit | PASS |
| 13 | Pass semantics: own K, decay to the day, NC counts toward n and resets the clock, same-day pre-day ratings, input order irrelevant, peak from the 5th UFC bout, gender, best win named from the store, export columns | `test_history_rating.py` (pass section) | unit | PASS |
| 14 | Golden cases (12) match to 1e-9; `PARAMS` == the fixture's params and the display constants; the generator reproduces the file byte for byte | `test_golden_case[*]`, `test_params_match_the_golden_cases_and_the_display_layer`, `test_generator_reproduces_the_golden_case_file_byte_for_byte` | parity | PASS |
| 15 | `history_ratings` rates the store up to `as_of` with names | `test_history_ratings_rate_the_store_up_to_as_of` | integration | PASS |
| 16 | Full-history pass == `model/fighter_rating.parquet` (4,146 rows) to 1e-9 | `test_full_history_pass_reproduces_research_export` (`-m seed`) | parity | PASS |
| 17 | Display: no history / no bout → unrated; decay to now; anchor (n ≥ 3, 730 d; none → no shift); peak; tags; P4P 15/10 over everyone rated; best win on the display scale (name falls back to the id) | `test_history_display.py` | unit | PASS |
| 18 | `score` writes the fields from the history store, is byte-stable on rerun; fighters without bouts are unrated | `tests/score/test_score_stage.py::test_writes_rating_fields_from_the_history_store` | integration | PASS |
| 19 | Acceptance (c): 4 bouts since the export's `as_of` → rated, and higher than before them | `test_a_fighter_with_many_bouts_since_the_model_export_is_rated` | integration | PASS |
| 20 | Bouts counted and decay follow `--now` | `test_decay_and_the_bouts_counted_follow_now` | integration | PASS |
| 21 | No store → unrated, tiers still score, exit 0, store not created; `fighter_rating.parquet` is ignored | `test_without_a_history_store_fighters_are_unrated_and_tiers_still_score`, `test_the_model_exports_rating_file_is_not_read` | integration | PASS |

## Existing tests changed (intentional behaviour changes)

- `tests/ufcstats/test_parsers.py`: the `Result(...)` equality asserts now include `outcome`, and two tests were renamed (`…_and_a_draw_outcome`, `…_and_an_nc_outcome`).
- `tests/score/test_elo.py`: **deleted**. It tested the roll-forward (`roll_forward`, `State`, `Bout`, `states_from_export`, the null rule, `recent_fights` dedup, the prefix UFC rule), which is removed. Its formula and display tests were ported to `tests/history/test_history_rating.py` and `test_history_display.py`.
- `tests/score/test_rating_parity.py`: **deleted**. Golden cases replay through `rate` in `test_history_rating.py`. The fixture stays at `tests/score/fixtures/rating_cases.json`, unchanged.
- `tests/score/test_score_stage.py`: the five parquet-based rating tests were replaced by history-store tests (#18–21). The tier tests are untouched.
- `tests/test_layout.py`: `history` added to the stages `fightcard --help` lists.
- `pyproject.toml`: `seed` marker, deselected by default like `live`.

Adjustments to this run's own RED tests during GREEN (wrong assumptions about the fixture, not behaviour):
- The cached UFC 336 page has no bouts yet, so the test checks `status = upcoming` instead of a fight count.
- Cyborg's fighter page names her "Cristiane Justino", and the page name wins over the card's "Cris Cyborg".
- DOB is not asserted (see Known gaps).
- One test closed its sqlite connection.

## Known gaps and follow-ups

- **The store isn't fed by `scrape` yet.** Ratings are as fresh as the last `history ingest` of a cache directory. The roll-forward used to pick up card fighters' newest bouts from `recent_fights`. That's gone, so ops must ingest before `score`. The next step is for `scrape` to upsert the event pages it fetches (ELO.md Future).
- **Product decision open:** the 14 bouts research lacks are now rated (see Parity). Their display impact today is 6 fighters ±1, with no P4P change.
- **Pre-existing parser bug, not fixed here:** `parsers.parse_date` uses `%B`, so fighter-page DOBs with abbreviated months ("Jul 09, 1985") parse to `None`. Only fully spelled months ("May") work. That affects `Fighter.dob` on the site and `fighters.dob` in the store.
- Round stats (`rounds` table) are deferred, as the recon proposed.
- fight-card-website's `site/src/lib/schema.ts` can add the optional `outcome` to its `Result` schema. A non-strict zod object strips unknown keys, so it keeps validating without it (not checked from here).

## Rebase onto main (`5c0eabe`, Phase 3 publisher)

The SHAs above are the rebased ones. The counts in the task report are from before the rebase (Phase 2 on `c1efe63`).
After the rebase: `uv run pytest -q` → `466 passed, 1 skipped, 49 deselected`; `-m seed` → `4 passed`; `-m docker` →
`44 passed`. `tests/test_layout.py` now lists both `publish` and `history` among the CLI stages.

## Review fixes (PR #2 code review)

Six findings from an ecc code review. Context: `score` runs hourly and a non-zero exit means "don't publish", so the
last good `/v1/` stays live. Failing is safer than writing degraded data with exit 0. One RED → GREEN pair per
finding (or tight group). #5 changes no code, so it's a pin.

| # | Finding | RED | GREEN | RED output (excerpt) |
|---|---|---|---|---|
| 1+2 | Missing store → every rating null with exit 0; an empty / never-ingested store → traceback | `047a18d` | `4c966d4` | `assert 0 == 1` (missing, schema-only), `sqlite3.OperationalError: no such table: fights` (0-byte), `sqlite3.DatabaseError: file is not a database`, `SystemExit 2` (unknown `--no-history`): 16 failed |
| 6 | `f"file:{db}?mode=ro"` URI | `3df37a5` | `8afc197` | `a#b` → exit 1; `50%20off` → `unable to open database file`; `x?mode=rw` → `no such access mode: rw/history/history.sqlite?mode=ro`: 3 failed |
| 3 | No staleness signal | `df1f174` | `8e44678` | exit 0 with completed `e-late` missing from the store or there without results; no store line: 4 failed (the in-store / after-`--now` case passes, as it should) |
| 4 | Method but no W/L flag drops out silently | `98b601d` | `254f18f` | `history ingest` exit 0 with two flags stripped: 1 failed |
| 5 | Golden-case test is self-referential | (pin) `047c7c4` | n/a | passes as written (no rating change); mutation check: `W_MAJORITY` 0.79 → `1c2f2571b18791b6 rating: 1492.747… != 1493.527…`; `DAYS_PER_YEAR` 365.0 → `00b490982afd1b17 rating: 1527.851… != 1527.840…` |

Choices (#1):
- **Default `--history-db`** = `<--data>/../history/history.sqlite`, resolved from `--data`, not the cwd. `data/` and
  `history/` sit side by side in the repo and on the VPS, and `--data data` from the repo root gives the same path as
  before. An explicit `--history-db` is used as given.
- **Opt-out `--no-history`** (exclusive with `--history-db`): tiers only. The store isn't read, fighters.json isn't
  rewritten, and every rating field stays **as it is** (the last good values), not null.
- **Unusable** = not a file → "no history store at …"; `sqlite3.DatabaseError` on open/read, missing any of
  `events`/`fights`/`fighters`/`pages`, or 0 events → "history store … is unusable (not an ingested history store)".
  Either way it's one line on stderr, exit 1, nothing written, and the store is opened read-only via
  `Path.resolve().as_uri() + "?mode=ro"`.
- **Stale** (#3): a data/ event with `status == "completed"` dated up to `--now` that has no bout with an outcome in
  the store fails the run, with the events named. Each run also logs `history store <db>: newest completed event
  <date>, last page fetched <ts|unknown> (now <ts>)`. The line is informational. It has no threshold, because a
  threshold would be a product call.
- #4: `ingest` raises `MissingOutcome` inside its one transaction, so the store is unchanged. The stage prints the
  sorted fight ids and exits 1. The full research cache has 0 such bouts (checked: 1,288 events, 11,694 fights).

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| 22 | Missing store: exit 1, message names the path and `--no-history`, data/ byte-identical, store not created | `test_a_missing_history_store_fails_and_writes_nothing` | integration | PASS |
| 23 | 0-byte / garbage file: exit 1, exactly one line, no traceback, data/ unchanged; schema-only store: exit 1 | `test_an_unreadable_history_store_fails_with_one_line[empty,garbage]`, `test_a_never_ingested_history_store_fails` | integration | PASS |
| 24 | `--no-history`: tiers score, fighters.json byte-identical (existing ratings kept); exclusive with `--history-db` | `test_no_history_scores_tiers_and_leaves_rating_fields_as_they_are`, `test_no_history_and_history_db_are_exclusive` | integration | PASS |
| 25 | Default store is beside `--data`, whatever the cwd | `test_default_history_store_is_beside_the_data_dir_not_in_the_cwd` | integration | PASS |
| 26 | Store paths with `#`, `%`, `?mode=rw` open read-only and stay byte-identical | `test_a_history_store_path_with_uri_characters_is_opened_read_only[*]` | integration | PASS |
| 27 | A completed event up to `--now` missing from the store, or there without results: exit 1, named, nothing written; in store or after `--now`: exit 0 | `test_a_completed_event_*` | integration | PASS |
| 28 | Store line: newest *completed* event, max `pages.fetched_at` (or `unknown`), `--now` | `test_score_logs_the_stores_newest_completed_event_and_last_fetch_against_now`, `test_the_store_line_says_unknown_without_fetch_times` | integration | PASS |
| 29 | Ingest with a method but no outcome: exit 1, fight ids named, store dump unchanged | `tests/history/test_history_stage.py::test_a_completed_bout_with_a_method_but_no_outcome_fails_the_whole_ingest` | integration | PASS |
| 30 | 50 fighters' research export rows reproduced to 1e-9 from 623 frozen bouts, by default (no seed, no parquet); fixture covers 17 edge-case kinds | `test_rate_reproduces_research_export_rows_for_the_frozen_fighters`, `test_frozen_parity_covers_the_edge_cases` | parity | PASS |
| 31 | The frozen fixture is exactly what the seed store + export give | `test_frozen_parity_fixture_is_what_the_seed_store_and_the_export_give` (`-m seed`) | parity | PASS |

Existing tests changed: in `tests/score/test_score_stage.py`, the `score()` helper and `test_default_model_dir_and_now`
pass `--no-history` (tier tests, no store). Row 21's `test_without_a_history_store_fighters_are_unrated_and_tiers_still_score`
is **replaced** by #22–24 (behaviour reversed on purpose). `test_the_model_exports_rating_file_is_not_read` now uses a
store with unrelated fighters instead of a missing one.

Final runs: `uv run pytest -q` → `483 passed, 1 skipped, 50 deselected`; `-m seed` → `5 passed`; `-m docker` →
`44 passed`. Coverage (`--cov=fightcard.score.stage --cov=fightcard.history`): score/stage 99%, history 98–100%,
total 99%.
