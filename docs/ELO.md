# Fighter rating (Elo): spec

Agreed 2026-10-25 in a design session (branch `feat/elo`). **Not implemented yet.** This replaces the plain
K=32 Elo that the site shows today. The finish model's own `elo` feature is **not** changed.

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
- **Card:** `Elo 1690 · #4 P4P` when the fighter has a P4P rank, otherwise `Elo 1690`.
- **P4P rank:** top 15 men and top 10 women, among fighters with ≥5 UFC bouts and a bout in the last 12 months.
- **Tags:** `provisional` for fewer than 3 UFC bouts; `inactive` for no bout in the last 12 months.
- **Peak:** the highest rating reached after ≥5 UFC bouts. Shown in the tooltip.
- `null` / hidden when the rating is unknown, as today.

## Where it is computed (interim)

1. **The research repo** (`fight-auto-research`) computes the rating over full history and exports
   `model/fighter_rating.parquet`: `fighter_id, rating, peak, n_bouts, n_ufc_bouts, last_bout_date` as of
   `manifest.as_of`. The old `elo` column in `fighter_snapshot.parquet` stays, because the finish model uses it.
2. **The site's `score` stage** rolls it forward over `recent_fights` dated after `as_of`, using this formula:
   - decay up to each bout date, n-based K, and split/majority credit from `PastFight.method` (`S-DEC`, `M-DEC`);
   - re-anchoring, P4P rank, tags and peak are computed there too.
   - The existing "record change not covered → null" rule stays.
3. **Contract changes** (`models.py`, `docs/CONTRACT.md`) go through the orchestrator: `Fighter.elo` keeps its
   meaning as the displayed rating, and new fields are added for `elo_peak`, `p4p_rank` and the tags.

## Contract (pinned for the build)

Exact semantics. Both repos implement them, and `model/rating_cases.json` holds golden cases that both test against.

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
- **UFC bout** = an event whose name does not start with `DWCS` or `Road to UFC`, **case-insensitive** (ufcstats
  writes "Road To UFC 5.1"). This agrees with the research build's `is_ufc` on every event.
- **Anchor:** `shift = 1500 − mean(decayed-to-now raw rating)` over fighters with n ≥ 3 and a bout in the last
  730 days. Every displayed value (current and peak) = raw + shift, rounded.
- **Eligibility for P4P rank:** ≥5 UFC bouts and a bout within 365 days of now. Men rank 1–15 and women 1–10 by
  displayed rating. Gender comes from the export.
- **Tags:** `elo_provisional` = fewer than 3 UFC bouts; `elo_inactive` = no bout within 365 days of now.
- **Best win** = among the fighter's wins in scope (split and majority wins included; draws, NC, CNC and
  Overturned excluded), the opponent with the highest **pre-bout** rating (decayed to that day). On a tie, the
  most recent bout. Raw value stored; displayed = raw + shift, like everything else.

**Research export** `model/fighter_rating.parquet` (one row per fighter with ≥1 bout in scope), as of `as_of`:

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

`manifest.json` gains `"rating": {"format_version": 1, "params": {...}}`.

**Site `Fighter` fields** (written by `score`):
- `elo: int | null`: displayed current rating.
- `elo_peak: int | null`
- `p4p_rank: int | null`
- `elo_provisional: bool` (default false)
- `elo_inactive: bool` (default false)
- `best_win: {opponent_id: str, opponent: str, elo: int, date: "YYYY-MM-DD"} | null`, where `elo` is the
  opponent's rating going into that bout on the display scale.
  **Data only; not displayed yet.** It's an option for the Elo display discussion (e.g. "Best win: Blachowicz
  1578" next to the peak in the tooltip). Added 2026-10-25 so it's ready if the display uses it. The user chose
  to keep the number a pure Elo and handle "untested" through display, not the formula.

Roll-forward: start every fighter in the export from their row. Apply card fighters' `recent_fights` after
`as_of` with the rules above (an opponent missing from the export starts as a debutant: 1500, n=0). Decay
everyone to now, then anchor and rank over the whole export population. Keep the existing null rule (unseen
bouts → `elo` null, and that fighter is left out of the ranking).
Known limitation: fighters off our cards who fought after `as_of` stay stale until the next re-export.

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

- **A permanent, cleaner home for the rating** (user requirement). The interim split, with the research export
  plus the site's roll-forward over at most 3 recent fights, means the formula lives in two repos, and fighters
  go `null` when there are too many unseen bouts. Target: a single implementation that owns the full bout history,
  e.g. a shared package or a site-side history store fed by the scraper, so there's no roll-forward.
- **Rank tiers** (chess or video-game style, e.g. "GM" for very high ratings), as a separate discussion.
  FIDE-style "title kept once earned" fits the peak.
- Revisit pre-UFC history (Sherdog/Tapology) if a source becomes practical.

Backtest scripts (scratchpad, not in the repo): `engine3.py`, `damp3*.py`, `lists_final.txt` under the session
scratchpad `elo-backtest/` directory.
