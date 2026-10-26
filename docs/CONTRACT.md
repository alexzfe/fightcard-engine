# Contract

Two layers:

1. **The public `/v1/` API**: what consumers (the site, anyone else) rely on. Additive-only.
2. **`data/`**: the files the pipeline stages pass to each other, which `fightcard publish` turns into `/v1/`.

Source of truth for the JSON types: `src/fightcard/models.py` (pydantic), published as `/v1/schema.json`. A
gentler walk-through of the API is in [API.md](API.md).

## /v1/ API

Base URL `https://ufc-data.xlobax.com`. Public, read-only, static files: nginx
(`deploy/nginx/fightcard-engine.conf`) serves the tree `fightcard publish` writes.

| URL | Content | Content-Type | Cache-Control |
|---|---|---|---|
| `/v1/index.json` | `data/index.json` plus `contract_version` (below). `generated_at` is when `scrape` built the data (ISO 8601 UTC with `Z`). | `application/json; charset=utf-8` | `public, max-age=60` |
| `/v1/events/{event_id}.json` | one `Event`, as `data/events/<id>.json` | `application/json; charset=utf-8` | `public, max-age=300` |
| `/v1/fighters.json` | `{ "<fighter_id>": Fighter, ... }`, as `data/fighters.json` | `application/json; charset=utf-8` | `public, max-age=300` |
| `/v1/images/{fighter_id}.webp` | headshot, with whatever extension `data/images/` holds (webp today; png/jpg/jpeg/avif/gif also served). `Fighter.image` is the path relative to `/v1/`, e.g. `images/<id>.webp`. | `image/webp` (`image/png`, …) | `public, max-age=86400` |
| `/v1/schema.json` | JSON Schema (draft 2020-12) of the JSON above: `#/$defs/Index`, `#/$defs/Event`, `#/$defs/Fighters`, `#/$defs/Fighter` | `application/json; charset=utf-8` | `public, max-age=300` |
| `/v1/snapshot.tar.gz` | every file above, from one generation, in one archive | `application/gzip` | `public, max-age=60` |
| `/healthz` | `ok`, for the container healthcheck (unversioned) | `text/plain` | `no-store` |

### Status codes and headers

- `200` for the resources above; `HEAD` works too.
- `304 Not Modified` for a conditional GET whose `If-None-Match` matches the `ETag` (or whose `If-Modified-Since`
  equals `Last-Modified`). It carries the same `ETag` and `Cache-Control`, no body.
- `404` for anything else, directories included (no listings). Body `{"error": "not found"}`, `application/json; charset=utf-8`, `Cache-Control: no-store`.
- `405` for any method but `GET`/`HEAD`, with `Allow: GET, HEAD` and a JSON body.
- No CORS headers: consumers fetch server-side. No `Server` version.
- `ETag` is nginx's `"<mtime>-<size>"` in hex. A file that didn't change between generations is shared with the
  previous one (hard link), so its `ETag` stays the same and clients keep their cached copy. A file that changed
  always gets a later mtime than the one it replaces, so its `ETag` always changes.

### Versioning

- `/v1/` is **additive-only**: new fields, new files, new endpoints. Never remove, rename or retype a field, or
  change what a value means. Such a breaking change ships as `/v2/`, served next to `/v1/` while consumers move.
- `index.json` carries `contract_version`, a string. It starts at `"1"`. An additive change bumps the minor
  part (`"1.1"`, `"1.2"`, …); the major part always matches the URL.
- Consumers must ignore fields they don't know.
- Changing `models.py` is a contract change: it must stay additive while `/v1/` is live, and this doc changes
  with it.

### Snapshot consistency

`snapshot.tar.gz` holds `index.json`, `events/`, `fighters.json`, `images/` and `schema.json` at their `/v1/`
paths, all from the same generation. Fetching the files one by one can straddle a publish (an index from the new
generation, an event from the old one); the snapshot can't. That's what the site's refresh downloads.

It is **deterministic**: sorted entries, mtime 0, uid/gid 0 with no names, fixed modes, gzip without a timestamp
or file name. Identical data gives a byte-identical file, and then publish keeps the live generation, so the
`ETag` doesn't change either.

### Formatting

All JSON is UTF-8, 2-space indent, keys sorted, trailing newline (stable diffs). Dates are `YYYY-MM-DD`;
timestamps are ISO 8601 UTC with `Z`.

### Publishing

`uv run fightcard publish --data data --out /srv/fightcard [--keep 5]`:

1. Validates `data/` with the pydantic models, and refuses (exit 1, nothing written, the live generation stays)
   when any file is unreadable or invalid, an event is filed under another id, a fighter is keyed under another
   id, `index.json` names an event with no `events/<id>.json`, a fight names a fighter missing from
   `fighters.json`, or `generated_at` isn't UTC with `Z`.
2. Writes the whole generation to `<out>/generations/.tmp-<id>/`, renames it to `<out>/generations/<id>/`, then
   points `<out>/current` at it by renaming a new relative symlink over the old one. Each rename is atomic, so a
   reader sees the old generation or the new one, never a half-written tree. A failed write exits 1 and leaves
   `current` alone.
3. `<id>` is `<UTC time>-<first 12 hex of the snapshot's sha256>`, so ids sort by publish time. If the snapshot
   matches the live generation's, nothing is written.
4. Keeps the newest `--keep` generations (default 5) and deletes older ones; leftovers of a crashed run are
   cleared first. One publisher per `<out>` at a time.

Files are `0644` and directories `0755` whatever the umask, since nginx runs as another user.

## data/ layout (pipeline output, publish input)

```
data/
  index.json                 Index
  events/<event_id>.json     Event (fights in card order, main event first)
  fighters.json              { "<fighter_id>": Fighter, ... }
  images/<fighter_id>.<ext>  headshots (webp today)
  images/misses.json         { "<fighter_id>": "<ISO last tried>" }  — retry after 7 days; not published
  stale_fighters.json        written by score when the model is stale; not published
```

Same formatting rule as above. Field meanings, which `/v1/` inherits:

- `finish_tier`: `"high" | "medium" | "low" | null`. `null` renders as "—".
- `segment`: `"main" | "prelims" | "early_prelims" | null`, from ESPN's fightcenter (`main`, `prelims1`, `prelims2`). `null` means unknown; the site shows no segment header for it. Scrape keeps it for fights still on the card.
- Once `tier_frozen_at` is set, nothing may change `finish_tier` again.
- `Fight.result.outcome`: `"win" | "draw" | "nc" | null`, from ufcstats' W/L flag. `winner` is `null` for both a draw and a no contest; `outcome` tells them apart. `null` in data written before the field existed (2026-10-26) or when the page shows no flag. Optional, so older data still validates.
- `index.model`: the vendored export's `as_of` and `version`, plus the summary the how-it-works page quotes (`train_rows`, `trees`, `low_max`, `medium_max`, `oos`, `tiers`, `importance` by theme). Written by `score`; the summary fields are null in data written before it.
- Fighter rating (`docs/ELO.md`), all written by `score`; the site shows it next to the record. It comes from the full-history pass over the history store (`score --history-db`). When the rating is unknown (no bout in scope) every field is `null` / `false`. A missing or stale store fails `score` instead (see Pipeline CLI), so it never nulls every fighter.
  - `Fighter.elo`: integer displayed current rating (decayed to now, anchored so active established fighters average 1500), or `null`.
  - `Fighter.elo_peak`: integer career peak on the same displayed scale (from the 5th UFC bout on, undecayed), or `null`.
  - `Fighter.p4p_rank`: `1`–`15` for men, `1`–`10` for women (≥5 UFC bouts and a bout in the last 365 days), or `null`.
  - `Fighter.elo_provisional`: `true` with fewer than 3 UFC bouts. `Fighter.elo_inactive`: `true` with no bout in the last 365 days.
  - `Fighter.best_win`: `{"opponent_id", "opponent", "elo", "date"}` or `null` (no win, or an export without the best-win columns). The beaten opponent rated highest going into the bout (split/majority wins count; draws, NC, CNC and Overturned don't; a tie goes to the most recent). `elo` is that pre-bout rating on the displayed scale. The site shows it under Stats.

## model/ layout (vendored export from fight-auto-research)

```
model/
  model.txt               LightGBM booster
  thresholds.json         { "low_max": float, "medium_max": float, "tier_finish_rates": {...} }
  fighter_snapshot.parquet  one row per fighter id; includes record w/l/d/nc as of export
  feature_spec.json       ordered features + how to assemble a bout row
  manifest.json           { "as_of": "YYYY-MM-DD", "version": str, "research_sha": str, "rating"?: {...} }
  fighter_rating.parquet  research's former rating export; no longer read (score uses the history store)
```

Columns and recipes are documented in `model/README.md`.

## Pipeline CLI

Run from the repo root: `uv run fightcard --data data <stage>`.

| Stage | Package | Reads | Writes |
|---|---|---|---|
| `scrape` | `fightcard.ufcstats` | ufcstats.com, data/ | events/, fighters.json, index.json |
| `images` | `fightcard.images` | index.json, events/, fighters.json, ESPN, Wikipedia | images/, `Fighter.image` (cards in `index.upcoming` then `index.recent`) |
| `segments` | `fightcard.segments` | events/, fighters.json, ESPN | `Fight.segment` (upcoming events only) |
| `history ingest` | `fightcard.history` | a ufcstats HTML cache (`--html-dir`) | the history store (`--db`, SQLite), optional sorted JSONL dump |
| `score` | `fightcard.score` | model/, events/, fighters.json, the history store (`--history-db`, read-only; default `<--data>/../history/history.sqlite`) | `Fight.finish_tier`, `tier_frozen_at`, `index.model`, `data/stale_fighters.json`, `Fighter.elo`, `elo_peak`, `p4p_rank`, `elo_provisional`, `elo_inactive`, `best_win` |
| `publish` | `fightcard.publish` | data/ | `<out>/generations/<id>/`, `<out>/current` (the `/v1/` tree) |

- Each stage package exposes `stage.py` with `add_arguments(parser)` and `run(args) -> int` (see `fightcard/cli.py`).
- Non-zero exit means: do not publish.
- `history ingest` exits 1 without `listings/events_completed.html` in the cache (it says which events are UFC events), or when a completed bout has a method but no W/L outcome (it names the fight ids), and leaves the store unchanged; one transaction per run.
- `score` resolves the default `--history-db` against `--data`, not the cwd: `<--data>/../history/history.sqlite` (`data/` and `history/` side by side, as in the repo and on the VPS). An explicit `--history-db` is used as given.
- `score` exits 1 before writing anything when the history store is missing, unusable (empty, not SQLite, missing tables, no events) or stale: a completed event in events/ dated up to `--now` has no bout with a result in the store. It prints one line saying which. Each run logs the store's newest completed event and last page fetch against `--now`.
- `score --no-history` (exclusive with `--history-db`) scores tiers only: the history store isn't read and every fighter's rating fields stay as they are in fighters.json.
- `scrape` must exit non-zero if it finds 0 upcoming events, if the soonest upcoming event has 0 fights, or if all upcoming events together have 0 fights. Far-out events with no bouts announced yet are not written and stay out of `index.upcoming` until fights appear.
- `segments` exits 0 when ESPN is down or an event can't be matched, and keeps the segments it already has. It exits 1 only if data/ is unreadable.
- `score` writes `data/stale_fighters.json` (list of `{fighter_id, name, event_id}`), or removes it when nothing is stale. `scripts/stale_issue.py` turns it into the body of a "Re-export finish model" issue.
- `publish` exits 1 on invalid data or a failed write; the live generation stays (see Publishing above).
