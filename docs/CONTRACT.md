# Shared contract

Source of truth for types: `pipeline/src/fightcard/models.py` (pydantic). This doc explains layout and ownership. Read `PLAN.md` for the why.

## data/ layout (pipeline output, site input; committed)

```
data/
  index.json                 Index
  events/<event_id>.json     Event (fights in card order, main event first)
  fighters.json              { "<fighter_id>": Fighter, ... }
  images/<fighter_id>.<ext>  headshots (png/jpg as downloaded; site converts to webp/avif)
  images/misses.json         { "<fighter_id>": "<ISO last tried>" }  — retry after 7 days
```

- All JSON is UTF-8, 2-space indent, keys sorted, trailing newline (stable diffs).
- `finish_tier`: `"high" | "medium" | "low" | null`. `null` renders as "—".
- `segment`: `"main" | "prelims" | "early_prelims" | null`, from ESPN's fightcenter (`main`, `prelims1`, `prelims2`). `null` means unknown; the site shows no segment header for it. Scrape keeps it for fights still on the card.
- Once `tier_frozen_at` is set, nothing may change `finish_tier` again.
- `index.model`: the vendored export's `as_of` and `version`, plus the summary the how-it-works page quotes (`train_rows`, `trees`, `low_max`, `medium_max`, `oos`, `tiers`, `importance` by theme). Written by `score`; the summary fields are null in data written before it.
- Fighter rating (`docs/ELO.md`), all written by `score`; the site shows it next to the record. When the rating is unknown (no `model/fighter_rating.parquet`, not in it and no newer bouts, or results since the export are missing) every field is `null` / `false`.
  - `Fighter.elo`: integer displayed current rating (decayed to now, anchored so active established fighters average 1500), or `null`.
  - `Fighter.elo_peak`: integer career peak on the same displayed scale (from the 5th UFC bout on, undecayed), or `null`.
  - `Fighter.p4p_rank`: `1`–`15` for men, `1`–`10` for women (≥5 UFC bouts and a bout in the last 365 days), or `null`.
  - `Fighter.elo_provisional`: `true` with fewer than 3 UFC bouts. `Fighter.elo_inactive`: `true` with no bout in the last 365 days.
  - `Fighter.best_win`: `{"opponent_id", "opponent", "elo", "date"}` or `null` (no win, or an export without the best-win columns). The beaten opponent rated highest going into the bout (split/majority wins count; draws, NC, CNC and Overturned don't; a tie goes to the most recent). `elo` is that pre-bout rating on the displayed scale. The site shows it under Stats.
- Dates are `YYYY-MM-DD`; timestamps are ISO 8601 UTC with `Z`.

## model/ layout (vendored export from fight-auto-research)

```
model/
  model.txt               LightGBM booster
  thresholds.json         { "low_max": float, "medium_max": float, "tier_finish_rates": {...} }
  fighter_snapshot.parquet  one row per fighter id; includes record w/l/d/nc as of export
  feature_spec.json       ordered features + how to assemble a bout row
  manifest.json           { "as_of": "YYYY-MM-DD", "version": str, "research_sha": str, "rating"?: {...} }
  fighter_rating.parquet  optional; one row per rated fighter as of export (see model/README.md)
```

The exact `feature_spec.json` and snapshot columns are defined by the model owner. Document them in `model/README.md` when they are set.

## Pipeline CLI

Run from the repo root: `uv run --project pipeline fightcard --data data <stage>`.

| Stage | Package | Reads | Writes |
|---|---|---|---|
| `scrape` | `fightcard.ufcstats` | ufcstats.com, data/ | events/, fighters.json, index.json |
| `images` | `fightcard.images` | index.json, events/, fighters.json, ESPN, Wikipedia | images/, `Fighter.image` (cards in `index.upcoming` then `index.recent`) |
| `segments` | `fightcard.segments` | events/, fighters.json, ESPN | `Fight.segment` (upcoming events only) |
| `score` | `fightcard.score` | model/, events/, fighters.json | `Fight.finish_tier`, `tier_frozen_at`, `index.model`, `data/stale_fighters.json`, `Fighter.elo`, `elo_peak`, `p4p_rank`, `elo_provisional`, `elo_inactive`, `best_win` |

- Each stage package exposes `stage.py` with `add_arguments(parser)` and `run(args) -> int` (see `fightcard/cli.py`).
- Non-zero exit means: do not commit, do not deploy.
- `scrape` must exit non-zero if it finds 0 upcoming events, if the soonest upcoming event has 0 fights, or if all upcoming events together have 0 fights. Far-out events with no bouts announced yet are not written and stay out of `index.upcoming` until fights appear.
- `segments` exits 0 when ESPN is down or an event can't be matched, and keeps the segments it already has. It exits 1 only if data/ is unreadable.
- `score` writes `data/stale_fighters.json` (list of `{fighter_id, name, event_id}`), or removes it when nothing is stale. CI uses that file to open or update the "Re-export finish model" issue.

## Ownership (parallel build)

Each owner edits only their paths. Contract changes (`models.py`, this doc, `pyproject.toml` deps, `cli.py`) go through the orchestrator.

| Owner | Paths |
|---|---|
| scraper | `pipeline/src/fightcard/ufcstats/`, `pipeline/src/fightcard/store.py`, `pipeline/tests/ufcstats/`, `pipeline/tests/fixtures/ufcstats/` |
| images | `pipeline/src/fightcard/images/`, `pipeline/tests/images/`, `pipeline/tests/fixtures/espn/`, `pipeline/tests/fixtures/wikipedia/` |
| segments | `pipeline/src/fightcard/segments/`, `pipeline/tests/segments/`, `pipeline/tests/fixtures/espn/fightcenter_*.json`, `pipeline/tests/fixtures/espn/scoreboard_calendar.json` |
| model | `~/Projects/fight-auto-research` (`export/` + tests, on branch `feat/export`), `model/`, `pipeline/src/fightcard/score/`, `pipeline/tests/score/` |
| site | `site/` |
| ops | `Dockerfile`, `.dockerignore`, `.github/`, `scripts/`, `~/Projects/infra` (branch `add-ufc-site`, no push) |

`store.py` (reading/writing data/ with the formatting rules above) is owned by scraper. Other stages may import it once it lands. Until then, read and write via `models.py` with the same formatting rules.
