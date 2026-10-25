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
- Once `tier_frozen_at` is set, nothing may change `finish_tier` again.
- Dates are `YYYY-MM-DD`; timestamps are ISO 8601 UTC with `Z`.

## model/ layout (vendored export from fight-auto-research)

```
model/
  model.txt               LightGBM booster
  thresholds.json         { "low_max": float, "medium_max": float, "tier_finish_rates": {...} }
  fighter_snapshot.parquet  one row per fighter id; includes record w/l/d/nc as of export
  feature_spec.json       ordered features + how to assemble a bout row
  manifest.json           { "as_of": "YYYY-MM-DD", "version": str, "research_sha": str }
```

The exact `feature_spec.json` and snapshot columns are defined by the model owner. Document them in `model/README.md` when they are set.

## Pipeline CLI

Run from the repo root: `uv run --project pipeline fightcard --data data <stage>`.

| Stage | Package | Reads | Writes |
|---|---|---|---|
| `scrape` | `fightcard.ufcstats` | ufcstats.com, data/ | events/, fighters.json, index.json |
| `images` | `fightcard.images` | fighters.json, ESPN, Wikipedia | images/, `Fighter.image` |
| `score` | `fightcard.score` | model/, events/, fighters.json | `Fight.finish_tier`, `tier_frozen_at`, `index.model`, `data/stale_fighters.json` |

- Each stage package exposes `stage.py` with `add_arguments(parser)` and `run(args) -> int` (see `fightcard/cli.py`).
- Non-zero exit means: do not commit, do not deploy.
- `scrape` must exit non-zero if it finds 0 upcoming events, if the soonest upcoming event has 0 fights, or if all upcoming events together have 0 fights. Far-out events with no bouts announced yet are not written and stay out of `index.upcoming` until fights appear.
- `score` writes `data/stale_fighters.json` (list of `{fighter_id, name, event_id}`), or removes it when nothing is stale. CI uses that file to open or update the "Re-export finish model" issue.

## Ownership (parallel build)

Each owner edits only their paths. Contract changes (`models.py`, this doc, `pyproject.toml` deps, `cli.py`) go through the orchestrator.

| Owner | Paths |
|---|---|
| scraper | `pipeline/src/fightcard/ufcstats/`, `pipeline/src/fightcard/store.py`, `pipeline/tests/ufcstats/`, `pipeline/tests/fixtures/ufcstats/` |
| images | `pipeline/src/fightcard/images/`, `pipeline/tests/images/`, `pipeline/tests/fixtures/espn/`, `pipeline/tests/fixtures/wikipedia/` |
| model | `~/Projects/fight-auto-research` (`export/` + tests, on branch `feat/export`), `model/`, `pipeline/src/fightcard/score/`, `pipeline/tests/score/` |
| site | `site/` |
| ops | `Dockerfile`, `.dockerignore`, `.github/`, `scripts/`, `~/Projects/infra` (branch `add-ufc-site`, no push) |

`store.py` (reading/writing data/ with the formatting rules above) is owned by scraper. Other stages may import it once it lands. Until then, read and write via `models.py` with the same formatting rules.
