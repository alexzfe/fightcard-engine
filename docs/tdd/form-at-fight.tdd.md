# TDD evidence: form at fight time

**Source:** the past-event redesign agreed 2026-10-28 (site PR #23, `fight-card-website/docs/tdd/past-event-ui.tdd.md`).
A past card showed each fighter's `recent_fights`, which is today's and so counts the fights after the card; the site
now hides those tiles on completed fights. This adds the form each fighter brought into the fight, so the site can
show it again. **Branch:** `feat/form-at-fight`. Step 2 of 3 (step 3: the site shows `Fight.form`).

### User journeys
- As a visitor on a past card, I want each fighter's last three results going into that fight, not today's.
- As a visitor, a fighter's first UFC bout shows no tiles rather than made-up ones (the store doesn't have most
  regional fights).
- As an API consumer, `Fight.form` has the same shape as `recent_fights`, so the same code renders both.

### Design
- Computed every `score` run from the history store, like `Fight.elo`, never stored or frozen.
- Every bout with a result in the store counts, whatever the rating's scope (`rating_bouts` drops small non-UFC
  cards; a fighter's form, like their ufcstats page, doesn't).
- Strictly before the bout's day in the store (data/'s date when the store doesn't have the bout), so a card never
  counts toward its own form, even when data/ dates it a day later. Within an earlier day (an early-UFC tournament)
  the main event (first in card order, last to end) comes first.
- Built from the same read-only connection `score` already opens for the history pass.

### Checkpoints
| Stage | Commit | Command | Result |
|---|---|---|---|
| RED | d5edb79 | `uv run pytest -q --continue-on-collection-errors` | 18 failed, 1096 passed, 1 skipped; `tests/history/test_history_form.py` fails to collect (`No module named 'fightcard.history.form'`). Failing: the contract version (1.3, not 1.4) ×3; `Fight.form` not published or in the schema; the five `Fight.form` score tests; the six CONTRACT/API doc tests; two layout tests that collect every suite. |
| GREEN | 330471a | `uv run pytest -q` | 1121 passed, 1 skipped. One test added after RED (`test_form_is_taken_before_the_stores_day_for_the_bout`): it pins the store's day, which the first draft didn't use. No RED test changed. |

A synthetic store of 24,000 bouts (2,000 cards × 12, 4,500 fighters) loads into `Form` in 0.11 s; 400 lookups take
1 ms.

### Test specification
| # | What is guaranteed | Test |
|---|---|---|
| 1 | The last three results strictly before the day, newest first, from the fighter's side (`nc`, `draw`, `loss`), with opponent name and id, method, round, time, event and date | `tests/history/test_history_form.py::test_the_last_three_results_before_the_day_newest_first_from_the_fighters_side` |
| 2 | The bout on the day and later ones don't count | `…::test_the_bout_on_the_day_and_after_it_are_not_counted` |
| 3 | Fewer than three: what there is; a debut or unknown fighter: `[]` | `…::test_a_fighter_with_fewer_than_three_bouts_before_gets_what_there_is`, `…::test_a_debut_or_an_unknown_fighter_has_no_form` |
| 4 | A bout without a result doesn't count; a one-bout non-UFC card (out of the rating's scope) does | `…::test_a_bout_without_a_result_is_not_counted`, fixture's Feb card in #1 |
| 5 | Same earlier day: main event first | `…::test_bouts_on_the_same_earlier_day_come_main_event_first` |
| 6 | `score` writes both corners' form on a completed fight, never including the fight itself | `tests/score/test_score_stage.py::test_a_completed_fight_gets_each_fighters_last_three_results_before_it` |
| 7 | Store's day when data/ dates the card later | `…::test_form_is_taken_before_the_stores_day_for_the_bout` |
| 8 | Upcoming fights: `null`; a debut: empty lists; `--no-history` leaves it; written as `{red, blue}` | `…::test_upcoming_fights_have_no_form`, `…::test_a_debut_has_an_empty_form_not_a_missing_one`, `…::test_no_history_leaves_fight_form_as_it_is`, `…::test_fight_form_is_written_as_a_json_object` |
| 9 | Published, optional in the schema, `FightForm` requires `red` and `blue`; contract 1.4 | `tests/publish/test_publish_stage.py::test_fight_form_is_published_and_in_the_schema`, `…::test_index_adds_contract_version_and_keeps_generated_at`, `…::test_schema_describes_the_top_level_types`, `tests/publish/test_publish_rankings.py::test_the_contract_version_is_1_4` |
| 10 | CONTRACT.md: the 1.4 line, the field ("strictly before", "newest first", `PastFight`, "rating's scope", "empty"), the score row; API.md explains it | `tests/test_docs.py::test_contract_versioning_says_1_4_added_fight_form`, `…::test_contract_documents_fight_form`, `…::test_score_row_names_fight_form`, `…::test_api_doc_explains_fight_form` |

## Review fixes (PR #12: python-reviewer + code-reviewer; no CRITICAL/HIGH)

| Stage | Commit | Command | Result |
|---|---|---|---|
| RED | ebba486 | `uv run pytest -q tests/history/test_history_form.py tests/score/test_score_stage.py` | 3 failed, 77 passed: the opponent with no name read as its id; a win for neither corner counted as a loss for both; the store's day was taken for a different bout under the same fight id. The fourth new test (form after `--now`) passed: it pins documented behaviour. |
| GREEN | 416f6bc | `uv run pytest -q` | 1125 passed, 1 skipped. No test changed after RED. |

- Taken: the same-bout guard on the store's day (as `fight_elo`'s), `"Unknown"` for a nameless opponent (as the
  ufcstats parser), malformed wins left out, and docs on form vs. `--now`.
- Not taken: a strict `FightForm` (its `PastFight` entries would stay lax, so it buys little); tighter doc tests.
- For step 3 (site): `NEWER_FIELDS` needs `'Fight.form': '1.4'` and `schema.ts` a nullish `FightForm` on `Fight`.
