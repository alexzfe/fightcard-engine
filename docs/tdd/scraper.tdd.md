# TDD evidence: scraper (ufcstats stage)

- **Source plan:** `PLAN.md`, milestones 1 and 6 (pipeline side), plus the orchestrator brief `scraper.md`. The user journeys below come from those.
- **Date:** 2026-10-24
- **Branch:** `feat/v1`

## Step 0: live PoW spike — BLOCKED, not run

The spike would have made 3 requests: GET the upcoming page, POST the solved nonce to `/__c`, then GET the upcoming page again with the `_fmc` cookie. It was written (`scratchpad/spike/spike.py`) but never executed. The Claude Code auto-mode permission classifier refused it as "Bot Check Circumvention".

- **Live requests made by this agent:** none.
- **Primary path:** the Python solver (`pow.py`, wired into `client.py`), as `PLAN.md` specifies. It is unit-tested against the saved challenge page. That the POST really returns `_fmc` is **still not verified live**.
- **Fallback:** `StealthyFetcher` is wired in as `client.stealthy_cookie`. It is called after two failed solves, using the same pattern as `fight-auto-research/scrapers/ufcstats/crawl.py`. It is tested only with a faked scrapling.
- **Human action needed:** the user must either allow the live spike and run, or run it themselves:
  `uv run --project pipeline fightcard --data "$(mktemp -d)" scrape`

### Fixtures

The missing fixtures came from the research repo's existing local cache at `fight-auto-research/data/raw_html/ufcstats/`, crawled 2026-10-14. That cost 0 live requests.

| Fixture | Source |
|---|---|
| `completed.html` | `listings/events_completed.html`, trimmed to the first 11 events. Row 1 is the `next.png` upcoming row. |
| `event_detail_completed.html` | `event-details/7f98d9d5a10fa25c.html` (UFC FN Allen vs. Duncan, 2026-10-10; 11 wins and 1 draw) |
| `fighter.html` | `fighter-details/b6452706b373eea1.html` (Renato Moicano, main event of the upcoming fixture) |

## User journeys

1. As the pipeline, I want ufcstats pages despite the JS proof-of-work, so the site can refresh without a browser.
2. As the ufcstats operator, I want at most one request per 3 s, with backoff and a halt when I push back.
3. As the site, I want `data/events/*.json`, `fighters.json` and `index.json` in the contract format, so builds are reproducible and diffs are stable.
4. As a visitor, I want results to appear next to the frozen pre-fight badge after an event.
5. As the operator, I want a bad scrape (0 events or 0 fights) to fail loudly without writing anything, so nothing broken is deployed.

## Task report

| Task | Summary | RED commit | GREEN commit |
|---|---|---|---|
| `ufcstats/pow.py` | detect, parse (nonce, difficulty, path), solve | `ddfa93d` (ModuleNotFoundError `fightcard.ufcstats.pow`) | `41af1c1` (27 passed) |
| `ufcstats/client.py` | injectable transport/clock/sleep/browser; 7-day `_fmc` file cache; ≥3 s spacing; 120 s backoff on 403/429/5xx, halts after 2 in a row; auto-solve with browser fallback after 2 failed solves | `ddfa93d` | `41af1c1` |
| `ufcstats/parsers.py` | event list (upcoming and completed), event detail with results, fighter page | `cbf67d4` (ModuleNotFoundError `parsers`) | `6284967` (51 passed) |
| `store.py` | atomic JSON with sorted keys, 2-space indent, trailing newline; typed load/save for events, fighters, index | `e428f82` (ModuleNotFoundError `fightcard.store`) | `ff45abb` (83 passed) |
| `ufcstats/stage.py` | upcoming → detail → events; past-upcoming and `--recent N` results; fighter details if missing or older than 30 days; merge-never-clobber; index; validation | `e428f82` | `ff45abb` |
| refactor | SoupStrainer for fighter pages (suite 8.6 s → 4.0 s), tests for the scrapling adapters, lint tidy | — | `0a19f64` (87 passed) |

Validation command for every step:

```
uv run --project pipeline pytest pipeline/tests/ufcstats --cov=fightcard.ufcstats --cov=fightcard.store --cov-report=term-missing
```

## Test specification

| # | Guarantee | Test | Type | Result |
|---|---|---|---|---|
| 1 | Challenge page detected; real pages not | `test_pow.py::test_detects_challenge_page`, `test_real_pages_are_not_challenges` | unit | PASS |
| 2 | nonce, difficulty (2) and path (`/__c`) parsed from the saved challenge | `test_pow.py::test_parses_nonce_difficulty_and_path` | unit | PASS |
| 3 | Solver returns the *smallest* n meeting the prefix; honours difficulty; bounded | `test_pow.py::test_solve_*` | unit | PASS |
| 4 | Challenge → POST `nonce`/`n` to `http://ufcstats.com/__c` → `_fmc` cached → re-GET with cookie | `test_client.py::test_challenge_is_solved_posted_and_cookie_cached` | unit | PASS |
| 5 | Cookie reused across clients; ignored after 7 days or if corrupt | `test_client.py::test_cached_cookie_*`, `test_cookie_older_than_seven_days_is_ignored`, `test_corrupt_cookie_file_is_ignored` | unit | PASS |
| 6 | Every request ≥3 s after the previous one; no wait before the first | `test_requests_are_spaced_at_least_three_seconds`, `test_first_request_does_not_wait` | unit | PASS |
| 7 | 403/429/500/503 → 120 s backoff then retry; two in a row → `Halt`; the streak resets after a success | `test_backs_off_120s_then_retries[*]`, `test_halts_after_two_bad_statuses_in_a_row`, `test_bad_status_counter_resets_after_success` | unit | PASS |
| 8 | Two failed solves → browser fallback; Halt if the browser gets no cookie or the challenge persists | `test_falls_back_to_browser_after_two_failed_solves`, `test_halts_when_*`, `test_unparseable_challenge_goes_straight_to_browser` | unit | PASS |
| 9 | Upcoming list: 7 events with id, name, ISO date, location, url | `test_parsers.py::test_parse_upcoming_list` | unit | PASS |
| 10 | Completed list parsed, including the `next.png` row (the stage filters it by date) | `test_parse_completed_list_includes_next_row_and_past_events`, `test_scrape_stage.py::test_recent_skips_next_row_and_future_events` | unit/integration | PASS |
| 11 | Event detail: name, slug, date, location, 13 fights in card order, fight ids from `data-link`, weight class, belt → title fight | `test_parse_upcoming_event_detail`, `test_title_fight_flag_from_belt_image`, `test_fight_without_data_link_gets_positional_id` | unit | PASS |
| 12 | Results: winner (from the W/L flag), method (first `<p>`), round, time; draw and NC have no winner | `test_parse_completed_event_detail_has_results`, `test_draw_has_no_winner`, `test_no_contest_has_no_winner`, `test_loss_flag_means_second_fighter_won` | unit | PASS |
| 13 | Fighter page: name, nickname, W-L-D (NC), height/reach in inches, stance, DOB ISO; `--` → null | `test_parse_fighter_page`, `test_parse_fighter_with_nickname_nc_and_missing_values` | unit | PASS |
| 14 | JSON: sorted keys, 2-space indent, trailing newline, UTF-8 (not `\u` escapes), atomic | `test_store.py::*` | unit | PASS |
| 15 | A fresh scrape writes 7 upcoming + 1 completed events, every card fighter once, and the index in date order | `test_scrape_stage.py::test_fresh_scrape_writes_events_fighters_and_index` | integration | PASS |
| 16 | 0 upcoming events, an upcoming event with 0 fights, a Halt, or an event-page HTTP error → exit 1 with **nothing written** | `test_zero_upcoming_events_fails_without_writing` and 3 siblings | integration | PASS |
| 17 | Merge keeps `finish_tier`/`tier_frozen_at` for surviving fights and drops removed fights | `test_keeps_tiers_for_fights_still_on_the_card` | integration | PASS |
| 18 | Fighter details fetched only if missing, older than 30 days, or the fighter just fought; `image` kept; unrelated fighters kept | `test_fresh_fighters_are_not_refetched_and_images_are_kept`, `test_newly_completed_event_refreshes_its_fighters`, `test_already_completed_event_does_not_refresh_fresh_fighters` | integration | PASS |
| 19 | A past upcoming event is re-scraped as completed; pre-fight corner order and tier kept | `test_past_upcoming_event_is_rescraped_as_completed` | integration | PASS |
| 20 | An event page is fetched once even when it is both stale and recent | `test_event_page_fetched_once_when_both_stale_and_recent` | integration | PASS |
| 21 | A past event with no results yet stays `upcoming` (retried next run) but leaves `index.upcoming` | `test_past_event_without_results_stays_upcoming_but_leaves_index` | integration | PASS |
| 22 | `index.model` (owned by score) preserved; `recent` most recent first; unlisted future events left alone | `test_index_keeps_model_info`, `test_recent_lists_completed_events_most_recent_first`, `test_future_event_no_longer_listed_is_left_alone` | integration | PASS |
| 23 | CLI: `fightcard --data X scrape --recent N --cookie-file F` wired through `cli.py` | `test_add_arguments_defaults`, `test_run_builds_client_and_scrapes`, `test_cli_dispatches_scrape` | integration | PASS |

## Coverage

```
pipeline/src/fightcard/store.py                  50      0   100%
pipeline/src/fightcard/ufcstats/client.py       133      0   100%
pipeline/src/fightcard/ufcstats/parsers.py      109      3    97%
pipeline/src/fightcard/ufcstats/pow.py           28      0   100%
pipeline/src/fightcard/ufcstats/stage.py        130      2    98%
TOTAL                                           450      5    99%
87 passed
```

## Design decisions and known gaps

- **Live behaviour unverified:** the PoW POST, the browser fallback and the end-to-end scrape have never run against the live site (see Step 0).
- **Stub metadata wins:** for upcoming events, the list row's name, date and location take precedence over the detail page. The fights come from the detail page.
- **Corner order:** completed cards list the winner first. When a fight id matches and the pair is swapped, the stored pre-fight red/blue order is kept, so the badge row doesn't flip after the event.
- **When an event counts as completed:** a re-scraped past event is `completed` only if at least one fight has a result. Otherwise it stays `upcoming` and is retried next run, but `index.upcoming` only lists events from today's upcoming list with `date >= today`.
- **Fighters who just fought:** when an event becomes completed, its fighters' pages are refetched even if their details are under 30 days old. This keeps records current for the score stage's staleness guard. On a re-run of an already-completed event they are not refetched.
- **Fighter page errors:** a fighter page returning a non-retryable HTTP error falls back to the name from the card, with `details_fetched_at = null`, so it is retried next run.
- **Cookie location:** `--cookie-file`, or `$XDG_CACHE_HOME/fightcard/ufcstats_cookie.json` (default `~/.cache/...`). CI should cache that path.
- **First live run:** with an empty `fighters.json`, expect about 180 fighter pages. At 3 s each that is about 10 minutes. Later runs only fetch new or stale fighters.
