# TDD evidence: rankings modules (Phase 1)

**Source plan:** inline `/plan` 2026-10-27 (site `/rankings/` tab: P4P only, top 15 men / top 10 women, headshots for
the top 15, search over active fighters). Phase 1 is the engine side: build every module, measure them, and decide
from the sizes what the site carries. Nothing is published yet.

## User journeys
- As the site, I want the P4P lists with names and divisions, so `/rankings/` can show them without the site
  computing Elo.
- As a visitor, I want to search any active fighter's Elo, so the site needs a compact index of active fighters.
- As the owner, I want division ranks and all-time peaks computed too, so we can see what they cost before deciding
  whether the page shows them.

## Checkpoints
| Stage | Commit | Command | Result |
|---|---|---|---|
| RED | d730355 | `uv run pytest -q tests/history/test_history_rankings.py` | collection error: `No module named 'fightcard.history.rankings'` |
| GREEN | 196b907 | same | 21 passed; full suite `uv run pytest -q` 717 passed, 1 skipped |

`display.fighter_ratings` was split into `scale`, `rankable` and `p4p_ranks` so the rankings and the fighter pages
share one implementation. `test_history_display.py`, `test_history_rating.py` and `tests/score` stayed green (119 passed).

## Guarantees
| # | What is guaranteed | Test |
|---|---|---|
| 1 | Division = latest bout's weight class; Catch/Open Weight and unknown fall back to the last real one; input order irrelevant | `test_division_*`, `test_catchweight_*`, `test_input_order_does_not_matter` |
| 2 | P4P is the top 15 men / top 10 women, same rank and Elo as the fighter pages | `test_p4p_lists_*`, `test_p4p_matches_the_fighter_pages_rank_and_elo` |
| 3 | P4P skips inactive (>365 days) and <5 UFC bouts | `test_p4p_skips_*` |
| 4 | Active index = UFC bout and active, best first, provisional flag and P4P rank, Elo equal to the pages | `test_active_index_*` |
| 5 | Division ranks: top 15 per division, P4P eligibility, no division → left out | `test_division_ranks_*` |
| 6 | All-time peaks include retired fighters, on the pages' scale, optional limit | `test_all_time_peaks_*` |
| 7 | Size report: rows plus compact JSON bytes raw and gzipped | `test_size_report_*` |
| 8 | `fightcard history rankings` exits 1 without a store and reports each module | `test_rankings_*` |

## Size report (seed store from fight-auto-research's cache, 1,288 events, as of 2026-10-27)
```
module             rows    raw KB   gzip KB
p4p                  25       2.4       0.9
active_index        644      77.5      16.3
division_ranks      164      11.5       4.3
all_time_peaks     1278      90.3      31.0
```

## Gaps
- Not published to `/v1/` and not on the site yet (Phase 2, after the size review).
- No `ruff` in the dev environment, so lint wasn't run.
- The seed cache is research's copy and may trail the prod store by a few events, so prod numbers can differ slightly.

# Phase 2: publish the rankings (`/v1/rankings.json`, contract 1.1)

## User stories
- As the site, I want `/v1/rankings.json` (P4P men 15 / women 10 with photos, plus every active fighter) so the rankings page and the fighter search need no Elo code.
- As the owner, I want it written by `score` from the same history pass as the fighters' ratings, so the two never disagree.
- As an operator, I want `publish` to refuse a bad file or a photo that isn't published, and to keep working when the file is absent.

## Checkpoints
| Task | Stage | Commit | Command | Result |
|---|---|---|---|---|
| 1 score writes `data/rankings.json` | RED | ec5d4d0 | `uv run pytest -q tests/score/test_score_stage.py tests/history/test_history_rankings.py` | 6 failed in tests/score; collection error in test_history_rankings (`cannot import name 'published'`) |
| | GREEN | fac6985 | same; full suite `uv run pytest -q` | 0 failed; 729 passed, 1 skipped |
| 2 publish validates and serves it | RED | ec2b1f0 | `uv run pytest -q tests/publish tests/test_docs.py` | 29 failed |
| | GREEN | 6fad95b | same; full suite | 0 failed; 763 passed, 1 skipped |
| 3 images fetches P4P headshots | RED | 5da3e01 | `uv run pytest -q tests/images/test_images_stage.py` | 4 failed |
| | GREEN | f050d05 | same; full suite | 0 failed; 773 passed, 1 skipped |

Baseline before Phase 2: 717 passed, 1 skipped. Two of my own tests were wrong, not the code, and were fixed in the GREEN commit:
`test_rankings_are_dated_and_decayed_to_now` used a `now` 304 days after the last bout (needs more than 365), and
`test_a_p4p_fighter_missing_from_fighters_json_gets_the_file_only` compared the raw input with the re-saved file.

## Guarantees
| # | What is guaranteed | Test |
|---|---|---|
| 9 | `score` writes `rankings.json` in the contract shape; `as_of` is the `--now` date; each P4P entry's rank/elo/name equal the fighter's in fighters.json | `test_writes_rankings_json_as_the_contract_has_it`, `test_rankings_are_dated_and_decayed_to_now` |
| 10 | `active` = every fighter with a UFC bout and a bout in 365 days, best first, equal to the pages; rows carry no `image` | `test_rankings_active_lists_*`, `test_published_active_rows_never_carry_an_image` |
| 11 | Rankings cover fighters who are not in fighters.json; a nameless one shows the id | `test_rankings_cover_fighters_who_are_not_in_fighters_json` |
| 12 | P4P `image` = `images/<id>.webp` when that file is in data/, else null (a .png doesn't count) | `test_rankings_image_is_set_when_the_headshot_exists`, `test_published_p4p_entries_*` |
| 13 | `--no-history` writes nothing and removes nothing; a failed history run leaves an existing file; an unchanged run rewrites identical bytes | `test_no_history_*`, `test_a_failed_history_run_*`, `test_an_unchanged_run_rewrites_identical_rankings` |
| 14 | `publish` serves a valid `rankings.json` (formatted, in the snapshot, in `schema.json` as `Rankings`) and still publishes without one | `test_rankings_json_*`, `test_a_data_dir_without_rankings_json_still_publishes`, `test_schema_describes_rankings` |
| 15 | `publish` refuses (exit 1, nothing written) an unparseable/non-object file, wrong types, missing keys, ranks not 1..n in order, more than 15 men / 10 women, and a non-null P4P `image` that isn't among the published images | `test_an_invalid_rankings_json_is_refused[*]`, `test_a_p4p_image_*` |
| 16 | `contract_version` is `"1.1"`; CONTRACT.md and API.md name the file, its cache policy and the 1 / 1.1 difference | `test_the_contract_version_is_1_1`, `tests/test_docs.py` |
| 17 | `images` fetches P4P headshots after the cards, under the same misses and `--limit`; a P4P id outside fighters.json gets the file only; an id on a card and in the lists is looked up once; an unreadable `rankings.json` is ignored | `test_p4p_*`, `test_a_fighter_on_a_card_*`, `test_an_unreadable_rankings_json_is_ignored` |

## Decisions
- `--no-history`: rankings.json is neither written nor removed, matching how the fighters' rating fields are left alone. A stale file keeps its old `as_of`.
- `score` writes the file after fighters.json in the same run; a run that fails on the store exits before either.
- `images` runs before `score`, so it reads the previous run's list: a fighter new to the P4P lists gets a photo one run later, and `score` then sets `image` when the file exists. Documented in CONTRACT.md.
- The Rankings models are `strict` (no `"1"` for `1`) and require every key (`division`, `image`, `p4p_rank` are required but nullable), since the file is machine-written.
- The limits 15 / 10 live in the model as well as in `display` (`max_length`), so a change to `p4p_men` / `p4p_women` in the rating params needs the model updated too.

## Real data
`published()` over the seed store (as of 2026-10-27) validates against `Rankings`: 15 men, 10 women, 644 active rows, 82,247 bytes compact JSON, 16.9 KB gzipped (no photos yet; the contract's 0.9 + 16.3 KB figures hold).

## Gaps
- Not run against the prod store, not deployed, nothing pushed.
- No `ruff` in the dev environment, so lint wasn't run.
- The first real `images` run after deploy fetches nothing for P4P (no `rankings.json` yet); photos appear from the second cycle.

## Phase 2 fixes
Three follow-ups on the same branch; the rankings.json shape did not change.

| Fix | Stage | Commit | Command | Result |
|---|---|---|---|---|
| 1 images computes the P4P ids itself (same-cycle headshots) | RED | f625e17 | `uv run pytest -q tests/images/test_images_stage.py tests/service/test_runner.py` | 15 failed |
| | GREEN | 857f32b | same; full suite `uv run pytest -q` | 0 failed; 779 passed, 1 skipped |
| 2 + 3 publish: dangling image becomes null; P4P/active consistency | RED | 26e23d3 | `uv run pytest -q` | 12 failed (11 in tests/publish; the guard in tests/history passes both before and after) |
| | GREEN | cf72b3d | same | 0 failed; 791 passed, 1 skipped |

Baseline before the fixes: 773 passed, 1 skipped.

### Fix 1: same-cycle P4P headshots
- `images` opens the history store read-only (`--history-db`, default `<--data>/../history/history.sqlite`, the runner passes `state/history/history.sqlite` like it does to `score`) and runs `rating_bouts` / `names` / `rate` / `rankings.p4p` as of the run date (`now.date()`), the same functions `score` uses for `rankings.json`. It no longer reads `rankings.json` at all.
- P4P lookups come after the card fighters, under the same misses and `--limit`; a P4P id outside fighters.json still gets the file only.
- A missing, non-SQLite, never-ingested or otherwise unusable store logs a warning naming the path and the images stage carries on with the card fighters (exit 0). `process(..., history_db=None)` skips the lookup, so library callers and tests without a store are unaffected.
- Tests build a real store (five UFC bouts each, so the fighter is P4P-eligible) rather than mocking `p4p`. They cover: a fighter new to the lists is fetched with no `rankings.json` present, a stale `rankings.json` is ignored, the lists are as of the run date (a fifth bout after `now` doesn't count), the unusable-store fallbacks (missing / not sqlite / never ingested), the default path beside data/ whatever the cwd, and the runner's `images --history-db` argv.
- The old `test_an_unreadable_rankings_json_is_ignored` tests are gone: the file is not read.

### Fix 2: a dangling P4P image no longer refuses the generation
`publish` writes that entry with `image: null` (in `rankings.json` and in the snapshot) and prints `publish: WARNING: ... published as null: <id> (<image>)` to stderr. The file in data/ is not touched. The three old "is refused" tests became "is published as null" tests (missing file, non-published suffix, no images/ dir), plus a snapshot test and a no-warning-when-all-present test.

### Fix 3: P4P lists and `active` must agree
`Rankings` has a model validator, so `publish` refuses with the usual `rankings.json is invalid: ...` (exit 1, nothing written) when:
- a P4P id is not in `active`, or is there with another `p4p_rank`;
- an `active` row has a non-null `p4p_rank` and no P4P entry (men or women) has that id and rank;
- (added, same intent) an id is in both lists, which would make the site's `p4p.women` lookup ambiguous.
The shared `RANKINGS` fixture in `tests/publish/publish_helpers.py` was itself inconsistent (its P4P fighters g9 and f2 were not in `active`), so it now has them. `test_published_agrees_with_itself_by_construction` checks that real `published()` output passes the validator, and on the seed-store copy the result (15 men, 10 women, 644 active) validates.

### Decisions
- The consistency check lives in the model, not in `publish/build.py`, so any validation of `Rankings` (tests, a future consumer of the model) gets it, and it adds nothing to schema.json (a validator is not expressible there; CONTRACT.md says so in words).
- `images` tolerates every store failure with a warning rather than an exit code: the store only says whose headshots to fetch. `score` is still what fails on a bad store.

### Gaps
- Not run against the prod store; nothing pushed, no PR.
- The images stage now imports pandas through `history.rating` (it already ran in the same venv as `score`).
- `ruff` is not in the dev environment, so lint wasn't run.
