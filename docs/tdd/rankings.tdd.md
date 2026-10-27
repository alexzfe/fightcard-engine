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
