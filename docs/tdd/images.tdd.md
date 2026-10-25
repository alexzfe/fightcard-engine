# TDD evidence: images stage (PLAN milestone 2)

- **Source plan:** `PLAN.md` § Fighter images and the images brief.
- **Port source:** finish-finder `scraper/ufc_scraper/image_scraper.py`.
- Journeys were derived during this run.

## User journeys

1. As the pipeline, I want headshots downloaded once for fighters on upcoming cards, so that the site never hotlinks and every card shows photos.
2. As the pipeline, I want lookup misses recorded and retried weekly, not every run, so that ESPN and Wikipedia aren't hammered.
3. As the operator, I want the images stage to never fail CI because of a miss, so that deploys aren't blocked by a missing photo.

## Package layout (`pipeline/src/fightcard/images/`)

| Module | Role |
|---|---|
| `names.py` | Normalises names (accents, case, punctuation, hyphens, Jr./Sr./II). Fuzzy-matches with rapidfuzz at 0.85: every token of the shorter name must pair with a token of the longer one, by ratio ≥0.85 or a ≥3-letter prefix (Alex→Alexander). The nickname can substitute for the first name. A single-token name never matches. |
| `http.py` | `HttpClient` with an injectable transport, clock and sleep. Rate limit is ≥1 s per source (all ESPN hosts share one bucket). Transport exceptions become `status=0`. Default transport is stdlib `urllib`, so no new dependency. |
| `sources.py` | `ImageFinder.candidates()` lazily yields URLs in this order: 1) ESPN scoreboard athlete id (fetched once per run), else ESPN search id, giving `a.espncdn.com/.../full/{id}.png`; 2) Wikipedia `pageimages` thumbnail (600 px), but only if the page title matches the name (a "(fighter)" suffix is OK). |
| `download.py` | Pillow validation. Accepts PNG, JPEG or WEBP with the shorter side ≥64 px; everything else is rejected (placeholders, HTML, BMP). |
| `misses.py` | `data/images/misses.json`, `{id: ISO}`. Skipped for 7 days, retried at ≥7 days. Contract formatting. |
| `stage.py` | Reads and writes data/ through `fightcard.store` (misses.json uses `store.write_json`, which is atomic). `fightcard --data data images [--limit N]`: upcoming events only, soonest first, fighters without an image. Writes `images/<id>.<ext>` and sets `Fighter.image`. Exits 0 on misses and network failures, 1 if data/ is unreadable. |

## Task report

| Step | Command | Result |
|---|---|---|
| Live fixtures (one fetch each) | curl ESPN search "Islam Makhachev", Wikipedia hit "Islam Makhachev", Wikipedia miss "Noah Gugnon" | Saved to `tests/fixtures/espn/search_islam_makhachev.json`, `tests/fixtures/wikipedia/{hit_islam_makhachev,miss_noah_gugnon}.json` |
| Headshot pattern check (1 live request) | `curl a.espncdn.com/i/headshots/mma/players/full/3949584.png` | `200 image/png`, 600×436 RGBA |
| RED (`ddcca6b`) | `uv run --project pipeline pytest pipeline/tests/images` | 6 collection errors: `ModuleNotFoundError: fightcard.images.{names,http,sources,download,misses}`, `cannot import name 'stage'`. The intended missing implementation. |
| GREEN (`92f69f9`) | same | `63 passed`, coverage 96% |
| Refactor (`18394b9`) | same, `--cov=fightcard.images` | `68 passed, 1 deselected`, coverage **100%** (331 stmts) |
| Live smoke | `pytest pipeline/tests/images -m live` | `1 passed` |
| Full suite | `uv run --project pipeline pytest pipeline/tests` | `95 passed, 1 deselected` |

## Test specification

| # | Guarantee | Test | Type | Result |
|---|---|---|---|---|
| 1 | Matching ignores accents, case, punctuation, hyphens and generational suffixes | `test_images_names.py::test_normalize_*`, `test_exact_and_accent_insensitive_match`, `test_jr_on_one_side_still_matches` | unit | PASS |
| 2 | Token order, small typos (≥0.85), Alex→Alexander and nickname-as-first-name all match | `test_token_order_insensitive`, `test_small_typo_matches_at_085`, `test_short_first_name_prefix_matches`, `test_nickname_used_as_first_name_matches` | unit | PASS |
| 3 | Different people never match: Volkanovski≠Volkov, same surname with a different first name, surname alone | `test_different_people_do_not_match`, `test_same_surname_*`, `test_single_token_*` | unit | PASS |
| 4 | ≥1 s between requests per source; sources are independent; all ESPN hosts share one bucket | `test_images_http.py` | unit | PASS |
| 5 | Network errors become a miss, never an exception | `test_transport_errors_become_status_zero`, `test_finder_survives_source_errors`, `test_network_down_is_not_fatal` | unit/integration | PASS |
| 6 | Scoreboard ids are used first, scoreboard fetched once, search skipped on a hit | `test_finder_uses_scoreboard_before_search`, `test_finder_skips_search_when_scoreboard_matched` | integration (fixture) | PASS |
| 7 | Order is ESPN search → Wikipedia; a Wikipedia page with a different title is rejected | `test_finder_falls_back_*`, `test_parse_wikipedia_*` | integration (fixture) | PASS |
| 8 | Only real images ≥64 px are accepted; a placeholder falls through to the next candidate | `test_images_download.py`, `test_placeholder_image_falls_through_to_next_candidate` | unit/integration | PASS |
| 9 | Only upcoming-event fighters without an image are processed, soonest event first | `test_skips_fighters_with_images_and_past_events`, `test_limit_caps_lookups` | integration | PASS |
| 10 | Misses recorded; skipped within 7 days, retried after, cleared on a hit | `test_images_misses.py`, `test_miss_is_recorded_*`, `test_old_miss_is_retried_*` | unit/integration | PASS |
| 11 | `fighters.json` and `misses.json` follow the contract format (sorted, 2-space, trailing newline); no writes when nothing changed | `test_fighters_json_is_written_per_contract`, `test_record_and_clear_then_save_*`, `test_no_writes_when_nothing_to_do` | integration | PASS |
| 12 | CLI `images --limit N` is wired up; exit 0 on misses, 1 on unreadable or malformed data/ | `test_cli_wires_images_stage_with_limit`, `test_run_exits_*` | integration | PASS |
| 13 | Downloads are stored as WebP, at most 320 px wide, aspect kept, never upscaled, alpha kept, EXIF/ICC stripped, much smaller than the source PNG | `test_normalize_*`, `test_downloaded_headshot_is_stored_as_small_webp` | unit/integration | PASS |
| 14 | Legacy png/jpg files in data/images are converted to WebP in place and `Fighter.image` is updated, with no network. Idempotent; `misses.json` and unreadable files are left alone; `process()` migrates first | `test_existing_png_and_jpg_are_migrated_*`, `test_migration_*`, `test_process_runs_migration_first`, `test_migrate_does_not_rewrite_*` | integration | PASS |

## Live run (hand-made temp data dir, 6 real fighters)

`uv run --project pipeline fightcard --data <scratch>/live-data images`. Run time 20 s, exit 0.

- Volkanovski and Dvalishvili came from the ESPN scoreboard.
- Makhachev, Topuria and Pereira came from ESPN search.
- Sharabutdin Magomedov came from the Wikipedia fallback. I checked the image visually and it is the right fighter.

**Hit rate: 6/6 (100%).**

## Coverage and known gaps

- `--cov=fightcard.images`: 100%.
- The live check is behind `@pytest.mark.live`, so it is deselected by default.
- `misses.json` is written (as `{}`) after any run that had hits. This is harmless.
- Interim `_store.py` was replaced by `fightcard.store` once the scraper landed it. Tests still pass: 68 passed, 100% coverage.

## Cycle 2: WebP normalisation and migration (2026-10-25)

- **Why:** ESPN PNGs are 600×436 and about 280 KB each, so data/images had grown to 24 MB in git.
- **RED (`78c35d9`):** `test_images_download.py` failed with `ImportError: cannot import name 'normalize_image'`. Stage tests failed 12 times, with `'images/f1.png' == 'images/f1.webp'` assertion errors and a missing `stage.migrate_images`.
- **GREEN (`01bcf76`):** `download.normalize_image` produces WebP: width ≤320 (LANCZOS), quality 82, method 6, and `info` cleared so no metadata is written. `stage.migrate_images` converts png/jpg files in place, and `process()` runs it first.
  - `pipeline/tests/images`: 80 passed, coverage 100%.
  - Full suite: 206 passed.
- **Real run:** `uv run --project pipeline fightcard --data data images` converted 102 legacy headshots, with 0 hits, 0 misses and 4 skipped (misses still inside the retry window). No fetches.
  - **data/images went from 24 MB to 1.3 MB** (102 WebP files, 1,056,742 bytes, about 10 KB each).
  - Spot check: a 320×233 RGBA WebP with no EXIF or ICC.
  - All 102 `Fighter.image` entries now end in `.webp`.
  - data/ was not committed.
