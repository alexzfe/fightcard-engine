# Fighter rating (Elo): spec

Agreed 2026-10-25 in a design session (branch `feat/elo`); implemented the same day. It replaced the plain
K=32 Elo the site showed before. The finish model's own `elo` feature is **not** changed.

## What the number means

How good a fighter is **today**, plus a **career peak**. Parameters are tuned for out-of-sample forecasting
(log-loss on the winner). The official P4P list and the named cases below are sanity checks only, not targets.

## Formula

| Rule | Value |
|---|---|
| Scope | ufcstats history as the research repo builds it, including Strikeforce, PRIDE, WEC and DWCS "full_card" events |
| Start | 1500 for every debutant |
| Expected score | `E = 1 / (1 + 10^((Rb − Ra)/400))` |
| K | `K = 275 / (n + 5)^0.4`, where n = the fighter's prior bouts in scope and no floor. Each side uses its own K |
| Result score W | win 1, draw 0.5, loss 0. **Split-decision win 0.6, majority-decision win 0.8**; the loser gets `1 − W` |
| No contest, overturned | no update |
| Method of finish | no adjustment (KO, SUB and unanimous decision all count as W = 1) |
| Update | `R += K · (W − E)`, with all bouts on one day using the ratings from before that day |
| Inactivity | 10% per year pull toward 1500 from the day of the last bout, no grace period: `R = 1500 + (R − 1500) · 0.9^(years since last bout)` |
| Weight classes | one rating per fighter, with no handicap and no per-division blend |

A heavy favourite (≥ ~70 points ahead) can lose a few points on a split-decision win. This is intended:
they were expected to win clearly.

## Display

- **Re-anchor:** shift every displayed rating uniformly so that the mean of established fighters (≥3 prior
  bouts) is 1500. Ranks and gaps don't change.
- **Card:** `Elo 1690 · #4 P4P` when the fighter has a P4P rank, otherwise `Elo 1690`, on its own line under the record
  (2026-10-26, `docs/tdd/elo-ui.tdd.md`).
- **P4P rank:** top 15 men and top 10 women, among fighters with ≥5 UFC bouts and a bout in the last 12 months.
- **Tags:** `provisional` for fewer than 3 UFC bouts; `inactive` for no bout in the last 12 months.
- **Peak:** the highest rating reached after ≥5 UFC bouts. Shown under Stats with the best win, and in the Elo's tooltip.
- `null` / hidden when the rating is unknown, as today.

## Where it is computed

One implementation, in this repo, over the full bout history (since 2026-10-26, `docs/tdd/history-rating.tdd.md`):

1. **History store** (`src/fightcard/history/store.py`): a SQLite file with every ufcstats event, bout, fighter
   and the raw page each came from. `fightcard history ingest --html-dir <cache> --db <path>` fills it from a
   ufcstats HTML cache with the same parsers `scrape` uses; it was seeded from fight-auto-research's cache
   (`data/raw_html/ufcstats/`) and agrees with research's parsed CSVs on all 11,600 bouts. After that, each
   `fightcard scrape` keeps it current (`--history-db`, default `<--data>/../history/history.sqlite`, as for `score`): the full completed
   listing, every event page and every fighter page the run fetched go in through the same parse + upsert
   functions as `history ingest`, in one transaction committed before the data/ write (data/ is never ahead of
   the store). A failed run leaves the store as it was, and re-running on the same pages changes nothing.
   Completed events in data/ the store doesn't have in full are refetched, at most 20 a run; one whose page 404s
   or lacks an outcome is skipped with a warning and retried after 7 days;
   `score` then warns on it (naming the reason) instead of failing. A listed card with no bouts (all
   cancelled) is stored `completed`, so it counts as in full. Without the store, `scrape` exits 1 unless `--no-history`; like
   `history ingest`, it exits 1 on a completed bout on this run's cards with a method but no outcome. A fighter
   fresh in data/ (< 30 days) gets a full store row (bio, record) only at their next refresh.
2. **Rating pass** (`src/fightcard/history/rating.py`, ported from research `export/rating.py`): `rate` over
   `store.rating_bouts`, in date order, gives each fighter's raw rating, n, n_ufc, last bout, peak, gender and
   best win. Over the bouts research had, it reproduces research's last `fighter_rating.parquet` (4,146 fighters,
   as of 2026-10-14) exactly.
3. **Display** (`src/fightcard/history/display.py`): decay to now, anchor, P4P rank, tags, best win on the
   display scale. `score --history-db <path>` runs 2 and 3 over the bouts up to `--now` and writes the `Fighter`
   fields. The store defaults to `<--data>/../history/history.sqlite`. `score` exits 1 and writes nothing when the
   store is missing, unusable (empty, not SQLite, never ingested) or behind data/ (a completed event up to `--now`
   that the store doesn't have as `completed` with no bouts, or with at least one bout with a result), so the live ratings stay instead of going null. `--no-history` scores tiers
   only and leaves the rating fields as they are.

There's no roll-forward and no `null` for "too many unseen bouts": every bout in the store counts. The ratings
are as fresh as the store; `scrape` updates it before `score` reads it, so a card's results count from the
refresh that first sees them (`docs/tdd/scrape-history.tdd.md`). `model/fighter_rating.parquet` isn't
read any more. The finish model's own `elo` column in `fighter_snapshot.parquet` is separate and unchanged.

## Contract (pinned for the build)

Exact semantics. `src/fightcard/history/rating.py` implements them. `tests/score/fixtures/rating_cases.json` holds the
golden cases: it's the generator's own output (`uv run python -m fightcard.history.rating <path>`), byte for byte,
and research's copy is identical. The golden cases are a regression pin, not independent evidence: the same `rate`
wrote them. The independent evidence is parity with research's export: all 4,146 rows with the full store
(`-m seed`), and, in the default suite, `tests/fixtures/history/rating_parity.json`, which holds 50 fighters'
export rows (CNC, Overturned, NC, draws, split/majority/DQ wins, non-UFC bouts, same-day bouts, 3-year layoffs,
women, unknown gender, peak / no peak, no win, two long careers) and the 623 bouts they depend on. It's rebuilt
byte for byte under `-m seed` (`uv run python tests/history/history_helpers.py` regenerates it).

- **n** = all prior bouts in scope, **including** NC and draws. K for a bout = `275 / (n + 5)^0.4`, with n taken
  before the bout.
- **Decay** is continuous and applied lazily: before a fighter's next bout (or at display time),
  `R = 1500 + (R − 1500) · 0.9^(days_since_last_bout / 365.25)`. The stored rating is the undecayed
  post-bout value.
- **Same day:** first decay each fighter to the day, then compute every bout's change from those pre-day
  ratings, then apply all changes. n and the last-bout date advance after the day.
- **Split vs majority** comes from ufcstats: method `S-DEC` gives W 0.6 and `M-DEC` gives W 0.8 (research:
  `result_details` = Split/Majority). `U-DEC`, KO/TKO, SUB and DQ give W 1. Overturned and CNC count as NC.
- **Peak (raw)** = the highest post-bout rating at any bout (UFC or not) once the fighter has ≥5 UFC bouts,
  counting that bout. Undecayed. `null` until then.
- **UFC bout** = a bout on an event in ufcstats' completed-events listing (which also holds the TUF finales,
  "Noche UFC" and "Ortiz vs Shamrock 3"), or on an unlisted event whose name matches `^UFC\b(?! BJJ)` (UFC 1 and
  the other early cards). This is research's `event_source.csv` rule, and it agrees with its `is_ufc` on all
  1,280 events. The earlier name-prefix rule (not `DWCS` / `Road to UFC`) only worked on recent UFC-listing
  events: it called 368 PRIDE, Strikeforce, WEC, ... events UFC.
- **Scope** = every bout with a result on a UFC event, or on another card with ≥4 bouts with a result.
- **Anchor:** `shift = 1500 − mean(decayed-to-now raw rating)` over fighters with n ≥ 3 and a bout in the last
  730 days. Every displayed value (current and peak) = raw + shift, rounded.
- **Eligibility for P4P rank:** ≥5 UFC bouts and a bout within 365 days of now. Men rank 1–15 and women 1–10 by
  displayed rating. Gender comes from the history pass.
- **Tags:** `elo_provisional` = fewer than 3 UFC bouts; `elo_inactive` = no bout within 365 days of now.
- **Best win** = among the fighter's wins in scope (split and majority wins included; draws, NC, CNC and
  Overturned excluded), the opponent with the highest **pre-bout** rating (decayed to that day). On a tie, the
  most recent bout. Raw value stored; displayed = raw + shift, like everything else.

**Rating rows** (`rating.rate` / `history_ratings`; the shape of research's former export
`model/fighter_rating.parquet`), one per fighter with ≥1 bout in scope:

| column | type | meaning |
|---|---|---|
| `fighter_id` | str | ufcstats id |
| `rating` | float | raw post-bout rating after the last bout (undecayed) |
| `last_bout_date` | str `YYYY-MM-DD` | |
| `n_bouts` | int | bouts in scope, including NC |
| `n_ufc_bouts` | int | UFC-event bouts, including NC |
| `peak` | float or null | raw peak as defined above |
| `gender` | `"M"` / `"F"` / null | from weight classes (Women's → F) |
| `best_win_opponent_id` | str or null | null when the fighter has no win |
| `best_win_opponent` | str or null | opponent's display name |
| `best_win_rating` | float or null | opponent's raw pre-bout rating |
| `best_win_date` | str `YYYY-MM-DD` or null | |

`rating.PARAMS` (also in `rating_cases.json`) holds the constants, including the display ones.

**Site `Fighter` fields** (written by `score`):
- `elo: int | null`: displayed current rating.
- `elo_peak: int | null`
- `p4p_rank: int | null`
- `elo_provisional: bool` (default false)
- `elo_inactive: bool` (default false)
- `best_win: {opponent_id: str, opponent: str, elo: int, date: "YYYY-MM-DD"} | null`, where `elo` is the
  opponent's rating going into that bout on the display scale.
  Shown under Stats since 2026-10-26. The user chose to keep the number a pure Elo and handle "untested" through
  display, not the formula.

Every rated fighter is decayed to now, then anchored and ranked together. A fighter with no bout in scope is
unrated (`elo` null) and left out of the ranking.

## Validation (backtest, 2015-10 → 2026-10, 5,432 decided UFC bouts)

| | log-loss | Brier | winner accuracy |
|---|---|---|---|
| current site Elo | .6852 | .2461 | 55.4% |
| this spec (with split credit 0.5/0.75 during tuning; 0.6/0.8 tied it) | .6659 | .2367 | 59.8% |

What was tried and rejected, so it isn't re-litigated:
- Glicko-2: worse; it needs many games per rating period.
- Weight-class handicap: fitted to 0.
- Overall + per-division blend: tie at best, worse at w ≥ .5.
- Grace period before decay: clearly worse.
- Layoff penalty with recovery: worse on test.
- Finish multipliers, including FiveThirtyEight's autocorrelation correction: no gain.
- Smaller early K: worse.
- Debutants starting relative to the mean: train and test disagree.
- "Proven strength" (Elo minus an uncertainty margin by bout count): the typical move over a fighter's next 5 bouts
  only falls from ±100 (1 bout) to ±71 (15 bouts), so it barely changes the order. Win-streak newcomers aren't
  overrated on average either: they win 58.8% when predicted 58.5%, and against veterans 62.7% when predicted
  56.5%. Fighters who feel "untested" are really a strength-of-schedule question, hence `best_win`.

### Named-case checks (test cases for the implementation)

| Case | Expected behaviour (display scale, approximate) |
|---|---|
| Volkanovski's two LW losses to Makhachev, 2023 | about −45, then about −37; stays in the top tier |
| GSP's return vs Bisping, 2017-11 | decayed about 115 over 4 years; gains about +38 |
| Pereira's LHW title win vs Prochazka | about +49 |
| Topuria beats Oliveira at LW | about +38 |
| de Ridder's split win over Whittaker | small gain, Whittaker small loss (at 0.6 credit) |

Exact numbers have to be re-derived from the research implementation at 0.6/0.8 and pinned in tests.

## Future

- **Keep the scraped HTML.** `scrape` records each page's url, sha256 and fetch time in `pages`, but not the HTML
  itself, so a parser fix re-ingests the seed cache and re-fetches what the scrape added since. Saving the pages
  into a cache directory would make `history ingest` able to rebuild the whole store offline.
- Research's `export/rating.py` and its copy of the golden cases can go, and research can train from this
  history.
- **Rank tiers** (chess or video-game style, e.g. "GM" for very high ratings), as a separate discussion.
  FIDE-style "title kept once earned" fits the peak.
- Revisit pre-UFC history (Sherdog/Tapology) if a source becomes practical.

Backtest scripts (scratchpad, not in the repo): `engine3.py`, `damp3*.py`, `lists_final.txt` under the session
scratchpad `elo-backtest/` directory.
