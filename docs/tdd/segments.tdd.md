# TDD evidence: segments stage (card segments)

- **Source:** design-direction session, 2026-10-25. The site splits each card into Main Card / Prelims / Early Prelims. ufcstats has no segment data, so the segments come from ESPN.
- **Contract:** `Fight.segment: "main" | "prelims" | "early_prelims" | null` (`docs/CONTRACT.md`).

## User journeys

1. As a visitor, I see an upcoming card split into Main Card, Prelims and Early Prelims, the way it airs.
2. As the operator, an ESPN outage or a bout ESPN doesn't list never blocks a refresh, and never wipes segments already known.
3. As the operator, segments survive the scrape stage rewriting an event (including when results come in).

## Sources (checked live 2026-10-25)

| Endpoint | Used for |
|---|---|
| `site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard` → `leagues[0].calendar[]` | ESPN event id (`event.$ref` `/events/<id>`), label, `startDate` (UTC; Oct 31 local = `2026-11-01T00:00Z`) |
| `site.web.api.espn.com/apis/common/v3/sports/mma/ufc/fightcenter/<id>` → `cards.{main,prelims1,prelims2}.competitions[].competitors[].athlete.displayName` | Bouts by segment, one request per event |

The scoreboard returns **403 to any User-Agent containing a URL** (`fightcard/0.1 (+https://…)` → 403; `fightcard/0.1 (non-commercial fan site)` → 200; this is deterministic). The segments stage sends its own URL-free UA. `images.http.USER_AGENT` has the same problem for the images stage's scoreboard lookup (out of scope here; images falls back to ESPN search).

## Package layout (`pipeline/src/fightcard/segments/`)

| Module | Role |
|---|---|
| `espn.py` | `segment_of` (`main`→`main`, `prelims1`→`prelims`, `prelims2`→`early_prelims`, anything else → None). `parse_calendar` and `parse_card` turn junk into `[]`. `match_event` requires the date to be within ±1 day **and** the headliners (the text after `:`, normalised and accent-free) to have a token_sort_ratio ≥85. |
| `stage.py` | Upcoming events only. A fight matches an ESPN bout when both names match in either corner order, using `images.names.names_match` (nickname aware). Once a card is read, unmatched fights get `None`. If the calendar or card can't be read, nothing changes. An event file is written only when it changed. Exits 0 on network failures, 1 if data/ is unreadable. |
| `ufcstats/stage.py` `merge_event` | Now carries `segment` forward, like `finish_tier`. |

## Checkpoint commits

| Commit | Stage | Evidence |
|---|---|---|
| `c3ca766` | RED | `ModuleNotFoundError: No module named 'fightcard.segments'` (collection error). `test_merge_keeps_card_segments` FAILED. |
| `e5758f3` | GREEN | `238 passed, 1 deselected`. During GREEN, one test expectation was corrected against the saved fixture: Lebosnoyani vs Hasanov is on ESPN's **main** card, so the prelims example became Bekoev vs Petroski. |
| `bb76ee4` | RED → GREEN | The first live run logged `scoreboard returned 403`. `test_user_agent_has_no_url` failed, then passed with the URL-free UA. `239 passed, 1 deselected`. |
| `ce351b1` | data | Live backfill: `segments: 5 cards read, 52 fights tagged, 1 unmatched`. |

## Test specification (`pipeline/tests/segments/test_segments.py`)

| # | Guarantee | Test |
|---|---|---|
| 1 | `Fight.segment` defaults to None and rejects ESPN's raw names | `test_fight_segment_defaults_to_none_and_rejects_unknown_values` |
| 2 | `fightcard segments` is a registered stage | `test_segments_stage_is_registered_in_the_cli` |
| 3 | ESPN segment names map to contract values | `test_segment_names_map_to_contract_values` |
| 4 | Calendar parsing gives id, label and UTC date, and tolerates junk | `test_parse_calendar_*` |
| 5 | Event match: +1 day UTC drift, accents (Procházka), wrong date, wrong name | `test_match_event_needs_date_and_name` (5 cases) |
| 6 | Card parsing: UFC 333 = 5 main / 4 prelims / 5 early prelims, tolerates junk | `test_parse_card_*` |
| 7 | Either corner order matches. Yana Santos vs Luana Santos don't cross-match. A bout ESPN doesn't list → None | `test_process_sets_segments_matching_either_corner_order` |
| 8 | A fight ESPN moved is updated | `test_process_moves_a_fight_espn_now_places_elsewhere` |
| 9 | Scoreboard down, fightcenter 404, transport exception or bad JSON → known segments kept | `test_espn_failures_keep_known_segments_and_exit_zero` (4 cases) |
| 10 | Unmatched event: untouched, and its card is never fetched | `test_unmatched_event_is_left_alone` |
| 11 | Completed events: skipped, with no requests | `test_completed_events_are_skipped` |
| 12 | No-op runs don't rewrite files | `test_unchanged_event_is_not_rewritten` |
| 13 | `run` exits 0 when ESPN is down and 1 when data/ is missing | `test_run_exits_zero_on_network_failure_and_one_on_missing_data` |
| 14 | The UA has no URL, and fightcenter params are encoded | `test_user_agent_has_no_url` |
| 15 | Scrape keeps segments when rewriting an event | `tests/ufcstats/test_scrape_stage.py::test_merge_keeps_card_segments` |

Coverage of `fightcard.segments`: 96%. Ruff is clean on the new files (the other 24 ruff findings in `pipeline/` predate this change).

## Live backfill (2026-10-25)

| Event | main | prelims | early_prelims | unmatched |
|---|---|---|---|---|
| UFC Fight Night: Moicano vs. Nolan | 6 | 7 | 0 | 0 |
| UFC Fight Night: Bonfim vs. Brady | 7 | 5 | 0 | 1 |
| UFC 334: Gane vs. Hokit | 5 | 4 | 3 | 0 |
| UFC Fight Night: Prochazka vs. Stirling | 5 | 4 | 0 | 0 |
| UFC 335: Oliveira vs. Lopes | 3 | 0 | 3 | 0 |

- **Unmatched:** ufcstats `Jonny Parsons vs Jose Souza` = ESPN `Jose Henrique vs Jonny Parsons` (the same fighter under a different surname). It stays `null`.
- **UFC 335:** ESPN currently has no `prelims1` bouts, only `prelims2`, so its non-main fights are early prelims as ESPN labels them.
- **UFC 333** (completed) wasn't processed, so its segments are `null`.
