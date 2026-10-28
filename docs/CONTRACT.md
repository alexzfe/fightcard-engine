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
| `/v1/rankings.json` | the P4P lists and the active-fighter index, as `data/rankings.json` (below). Optional: absent until `score` has written it. | `application/json; charset=utf-8` | `public, max-age=300` |
| `/v1/images/{fighter_id}.webp` | headshot, with whatever extension `data/images/` holds (webp today; png/jpg/jpeg/avif/gif also served). `Fighter.image` is the path relative to `/v1/`, e.g. `images/<id>.webp`. | `image/webp` (`image/png`, …) | `public, max-age=86400` |
| `/v1/schema.json` | JSON Schema (draft 2020-12) of the JSON above: `#/$defs/Index`, `#/$defs/Event`, `#/$defs/Fighters`, `#/$defs/Fighter`, `#/$defs/Rankings` | `application/json; charset=utf-8` | `public, max-age=300` |
| `/v1/snapshot.tar.gz` | every file above, from one generation, in one archive | `application/gzip` | `public, max-age=60` |
| `/healthz` | `ok`, for the container healthcheck (unversioned) | `text/plain` | `no-store` |

### Status codes and headers

- `200` for the resources above; `HEAD` works too.
- `304 Not Modified` for a conditional GET whose `If-None-Match` matches the `ETag` (or whose `If-Modified-Since`
  equals `Last-Modified`; Cloudflare also answers `304` to a later date). It carries the `ETag` (in either form
  behind Cloudflare, see below) and `Cache-Control`, no body. A client sends the `ETag`
  (and `Last-Modified`) back exactly as it received them; see "ETags through Cloudflare" below.
- `404` for anything else, directories included (no listings). Body `{"error": "not found"}`, `application/json; charset=utf-8`, `Cache-Control: no-store`.
- `405` for any method but `GET`/`HEAD`, with `Allow: GET, HEAD` and a JSON body.
- No CORS headers: consumers fetch server-side. No `Server` version.
- `ETag` is nginx's `"<mtime>-<size>"` in hex. A file that didn't change between generations is shared with the
  previous one (hard link), so its `ETag` stays the same and clients keep their cached copy. A file that changed
  always gets a later mtime than the one it replaces, so its `ETag` always changes.

### ETags through Cloudflare

The public host, `https://ufc-data.xlobax.com`, is nginx behind Cloudflare. nginx doesn't compress, and it sends a
strong `ETag`; Cloudflare compresses the JSON itself and may weaken the `ETag` (RFC 9110: its bytes needn't be the
file nginx fingerprinted), on compressed and uncompressed responses alike.

The JSON's `ETag` may come back weak (`W/"<mtime>-<size>"`) or strong (`"<mtime>-<size>"`) whatever the
`Accept-Encoding`. Which one you get depends on the copy Cloudflare holds, can differ between files and changes
over time: on 2026-10-28, gzip responses were strong for every file checked, but earlier that day events and
`rankings.json` were weak over gzip too; `br`, `zstd`, `identity` and no `Accept-Encoding` were weak.
`snapshot.tar.gz` and the headshots are already compressed, get no `Content-Encoding` and are always strong.

`Last-Modified` and the opaque part between the quotes are the same in every case; only the `W/` prefix varies.
Cloudflare compares `If-None-Match` weakly, so either form gets the `304`. The `304` carries an `ETag` in either
form, not necessarily the one you sent; keeping your stored value or replacing it with the 304's both work. A
client treats the `ETag` as an opaque string: it stores the header verbatim (`W/` included) and sends that back,
never strips, adds or rebuilds the prefix, and doesn't parse the mtime or size out of it. `Vary: Accept-Encoding`
is set on the JSON. `If-Modified-Since` with the `Last-Modified` it received also gets the `304`.

### Versioning

- `/v1/` is **additive-only**: new fields, new files, new endpoints. Never remove, rename or retype a field, or
  change what a value means. Such a breaking change ships as `/v2/`, served next to `/v1/` while consumers move.
- `index.json` carries `contract_version`, a string. It starts at `"1"`. An additive change bumps the minor
  part (`"1.1"`, `"1.2"`, …); the major part always matches the URL.
  - `"1"`: the first `/v1/` publish.
  - `"1.1"`: `Result.outcome`, the fighter rating fields (`elo`, `elo_peak`, `p4p_rank`, `elo_provisional`, `elo_inactive`, `best_win`), the model summary in `index.model` and `index.model.stale_fighters`. All optional, so data written before them still validates.
  - `"1.2"`: adds `rankings.json` (the P4P lists and the active-fighter index). Nothing else changed.
  - `"1.3"`: adds `Fight.elo` (a completed fight's Elo going in, the bout's own change and the P4P rank going in).
    Optional, so events written before it still validate.
  - `"1.4"`: adds `Fight.form` (a completed fight's last three results per corner before it). Optional, so events
    written before it still validate.
- Enum values are closed within `/v1/`: `Event.status`, `Fight.finish_tier`, `Fight.segment`, `PastFight.result`,
  `Result.outcome` and `ModelInfo.tiers`' keys keep exactly the values they have. Consumers validate them strictly
  (the site does), so a new value in an existing enum is a breaking change and ships as `/v2/`. A new field may
  bring its own enum (additive). `tests/publish/test_v1_enums.py` pins every enum in `/v1/schema.json`.
- Consumers must ignore fields and files they don't know. The site's `scripts/fetch_v1.py` skips a file outside the
  layout it knows (and names it), so a new file needs no release order: the engine can publish it first, and the
  site reads it once a site release lists it. A new file is announced in a minor version
  (`"1.2"` added `rankings.json`).
- Changing `models.py` is a contract change: it must stay additive while `/v1/` is live, and this doc changes
  with it.

### Snapshot consistency

`snapshot.tar.gz` holds `index.json`, `events/`, `fighters.json`, `rankings.json` (when there is one), `images/`
and `schema.json` at their `/v1/` paths, all from the same generation. Fetching the files one by one can straddle a publish (an index from the new
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
   `fighters.json`, `generated_at` isn't UTC with `Z`, or `rankings.json` (when present) is invalid or its P4P
   lists and `active` disagree (below). A P4P `image` that isn't among the published images doesn't refuse: that entry
   is published with `image` `null` and a warning goes to stderr. A data/ without `rankings.json` publishes fine, just
   without it.
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
  rankings.json              Rankings; written by score, optional for publish
  images/<fighter_id>.<ext>  headshots (webp today)
  images/misses.json         { "<fighter_id>": "<ISO last tried>" }  — retry after 7 days; not published
  stale_fighters.json        written by score when the model is stale; not published
```

Same formatting rule as above. Field meanings, which `/v1/` inherits:

- `finish_tier`: `"high" | "medium" | "low" | null`. `null` renders as "—".
- `segment`: `"main" | "prelims" | "early_prelims" | null`, from ESPN's fightcenter (`main`, `prelims1`, `prelims2`). `null` means unknown; the site shows no segment header for it. Scrape keeps it for fights still on the card.
- Once `tier_frozen_at` is set, nothing may change `finish_tier` again.
- `Fight.result.outcome`: `"win" | "draw" | "nc" | null`, from ufcstats' W/L flag. `winner` is `null` for both a draw and a no contest; `outcome` tells them apart. `null` in data written before the field existed (2026-10-26) or when the page shows no flag. Optional, so older data still validates.
- `index.model`: the finish model that scored this generation: its `as_of` and `version` (`model.as_of`, `model.version`: the pulled model once `FIGHTCARD_MODEL_URL` is set, else the one baked into the image; `docs/SERVICE.md`), plus the summary the how-it-works page quotes (`train_rows`, `trees`, `low_max`, `medium_max`, `oos`, `tiers`, `importance` by theme). Written by `score`; the summary fields are null in data written before it.
  - `index.model.stale_fighters`: integer, how many distinct fighters the staleness guard nulled in this run (their record moved since the export, so their bouts get no tier); `0` when none. `null` in data written before it. Research's export gate reads it with `model.version`.
- Fighter rating (`docs/ELO.md`), all written by `score`; the site shows it next to the record. It comes from the full-history pass over the history store (`score --history-db`). When the rating is unknown (no bout in scope) every field is `null` / `false`. A missing or stale store fails `score` instead (see Pipeline CLI), so it never nulls every fighter.
  - `Fighter.elo`: integer displayed current rating (decayed to now, anchored so active established fighters average 1500), or `null`.
  - `Fighter.elo_peak`: integer career peak on the same displayed scale (from the 5th UFC bout on, undecayed), or `null`.
  - `Fighter.p4p_rank`: `1`–`15` for men, `1`–`10` for women (≥5 UFC bouts and a bout in the last 365 days), or `null`.
  - `Fighter.elo_provisional`: `true` with fewer than 3 UFC bouts. `Fighter.elo_inactive`: `true` with no bout in the last 365 days.
  - `Fighter.best_win`: `{"opponent_id", "opponent", "elo", "date"}` or `null` (no win, or an export without the best-win columns). The beaten opponent rated highest going into the bout (split/majority wins count; draws, NC, CNC and Overturned don't; a tie goes to the most recent). `elo` is that pre-bout rating on the displayed scale. The site shows it under Stats.
- `Fight.elo` (`"1.3"`, `docs/ELO.md`): the Elo at the time of a completed fight, written by `score` from the same
  history pass, e.g. shown as "1612 (+26) · #4 P4P".
  `{"red_pre", "red_delta", "blue_pre", "blue_delta", "red_p4p_rank", "blue_p4p_rank"}`, all integers (strict: no
  `"1612"` for `1612`), or `null`.
  - `red_pre` / `blue_pre`: the fighter's rating going into the bout (from the bouts before the card's day, decayed
    to it), on today's shift: the displayed scale of the run that wrote it, the same as `Fighter.elo`. The anchor
    moves as fighters come and go, so an old fight's values can move a point between publishes. They are
    recomputed every `score` run, never frozen, so a completed card's event file can change long after the card
    whenever the shift moves: a new `ETag` and `Last-Modified`, and a changed file in `snapshot.tar.gz`, with no
    news on the card itself.
  - `red_delta` / `blue_delta`: this bout's own change, rounded on its own (an early-UFC tournament fighter's bouts
    that day each show their own; they needn't add up to the day's change after rounding). `0` for a no contest,
    including CNC and Overturned.
  - `red_p4p_rank` / `blue_p4p_rank`: the P4P rank going into the card, i.e. the lists as of the day before (bouts
    strictly before the bout's day in the history store, the day `*_pre` is decayed to, the same rules as
    `Fighter.p4p_rank`), `1`–`15` men, `1`–`10` women, or `null` when unranked. A result on the card itself never
    moves it.
  - `null` for an upcoming fight, one with no result, and one the history store doesn't have under that fight id
    (store ids are the site's ids) or that is after `score --now`. `score --no-history` leaves it as it is. There is
    no record at the time (ufcstats records include regional bouts the store doesn't have).
- `Fight.form` (`"1.4"`): each corner's form going into a completed fight, written by `score` from the history store,
  so a past card shows the results its fighters brought in rather than today's `Fighter.recent_fights` (which would
  count the fights after it). `{"red": [PastFight, ...], "blue": [PastFight, ...]}`, or `null`.
  - Each list is that fighter's last results strictly before the bout's day in the history store (data/'s date when
    the store doesn't have the bout, so the bout never counts toward itself), newest first, at most 3, each a
    `PastFight` from their side (`win` / `loss` / `draw` / `nc`). Within an earlier day (an early-UFC tournament)
    the main event comes first.
  - It counts every bout with a result in the store, whatever the rating's scope (a small non-UFC card the rating
    leaves out still counts). Bouts the store doesn't have (most regional fights) don't, so a fighter's first UFC
    bouts can show fewer than 3, and a debut an empty list.
  - An opponent the store has no name for reads `"Unknown"`, as in `recent_fights`; a malformed win (the winner is
    neither fighter) is left out.
  - `null` for an upcoming fight or one with no result. Unlike `Fight.elo` it is set on a fight after `score --now`
    too (it only looks before the fight's own day). Recomputed every `score` run from the store; `score
    --no-history` leaves it as it is.

### rankings.json

`{ "as_of", "p4p": { "men": [...], "women": [...] }, "active": [...] }`, written by `score` from the same history
pass as the fighters' ratings (`fightcard.history.rankings.published`), validated by `Rankings` in `models.py`.

```json
{
  "as_of": "2026-10-27",
  "p4p": {
    "men":   [{"rank": 1, "id": "<ufcstats fighter id>", "name": "Islam Makhachev", "elo": 1818,
               "division": "Welterweight", "image": "images/<id>.webp"}],
    "women": [{"rank": 1, "id": "...", "name": "Natalia Silva", "elo": 1763,
               "division": "Women's Flyweight", "image": null}]
  },
  "active": [{"id": "...", "name": "...", "elo": 1700, "division": "Lightweight", "p4p_rank": 3,
              "provisional": false}]
}
```

- `as_of`: `YYYY-MM-DD`, the day the ratings are decayed to (`score --now`'s date).
- `p4p.men` (up to 15) and `p4p.women` (up to 10) are ordered by `rank`, 1..n. A fighter in both this and
  `fighters.json` has the same `rank` / `elo` as `Fighter.p4p_rank` / `Fighter.elo`; that holds after a history run
  (`score --no-history` leaves the rating fields as they are, and a scrape rebuilds a refreshed fighter without them,
  so `fighters.json` can lag until the next history run). The fighters need not be on a card, so they need not be in
  `fighters.json`.
- `elo`: integer on the displayed scale, as `Fighter.elo`. `name` falls back to the id when the history store has none.
- `division`: the weight class of the fighter's latest bout (Catch Weight, Open Weight and unknown classes skipped),
  or `null`.
- `image`: only on P4P entries. `images/<id>.webp` when that headshot is in `data/images/` when `score` runs, else
  `null`. `images` runs before `score` and works out the same P4P ids itself from the history store, as of its run
  date, so a fighter who enters the lists gets their headshot in the same run (a miss, or an unusable store, leaves
  `image` `null`). Publish never refuses a generation over `image`: a non-null `image` that isn't among the
  published images is published as `null`, with a warning on stderr. `active` rows never carry `image`.
- `active`: every fighter with at least one UFC bout and a bout in the last 365 days, highest rating first (the
  unrounded one, ties by id), so rows with an equal `elo` are not necessarily in id order. `p4p_rank` is the
  `Fighter.p4p_rank` value or `null`; `provisional` is true with fewer than 3 UFC bouts.
- The lists and `active` agree, and publish refuses a file where they don't: every P4P `id` is in `active` with
  `p4p_rank` equal to its `rank`; every `active` row with a non-null `p4p_rank` has a P4P entry (in `men` or `women`)
  of that `id` and `rank`; and no `id` is in both lists. So a consumer can look an `id` up in `p4p.women` (or `men`)
  to tell which list a ranked fighter is in.
- `score --no-history` neither writes nor removes it (like the rating fields), so after a history run it keeps
  that run's `as_of`; a run that fails on the history store changes nothing. Consumers treat the file as optional.
- Size on real data: P4P about 0.9 KB gzipped, `active` (about 650 rows) about 16 KB gzipped.

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
| `scrape` | `fightcard.ufcstats` | ufcstats.com, data/, the history store (`--history-db`) | events/, fighters.json, index.json; upserts the pages it fetched into the history store |
| `images` | `fightcard.images` | index.json, events/, fighters.json, the history store (`--history-db`, read-only; default `<--data>/../history/history.sqlite`; a missing or unusable one warns and leaves the cards only), ESPN, Wikipedia | images/, `Fighter.image` (cards in `index.upcoming` then `index.recent`, then the P4P ids as of the run date, worked out from the store; those without a fighters.json entry only get the file) |
| `segments` | `fightcard.segments` | events/, fighters.json, ESPN | `Fight.segment` (upcoming events only) |
| `history ingest` | `fightcard.history` | a ufcstats HTML cache (`--html-dir`) | the history store (`--db`, SQLite), optional sorted JSONL dump |
| `model pull` | `fightcard.model` | `$FIGHTCARD_MODEL_URL` (`latest.json`, `<version>/<file>`), the baked model/ (first boot seed) | `state/model/<version>/` and the `state/model/current` symlink (`--root`, default `<--data>/../model`); see `docs/SERVICE.md` |
| `score` | `fightcard.score` | model/, events/, fighters.json, the history store (`--history-db`, read-only; default `<--data>/../history/history.sqlite`) | `Fight.finish_tier`, `tier_frozen_at`, `index.model`, `data/stale_fighters.json`, `Fighter.elo`, `elo_peak`, `p4p_rank`, `elo_provisional`, `elo_inactive`, `best_win`, `Fight.elo`, `Fight.form`, `data/rankings.json` |
| `publish` | `fightcard.publish` | data/ | `<out>/generations/<id>/`, `<out>/current` (the `/v1/` tree) |

- Each stage package exposes `stage.py` with `add_arguments(parser)` and `run(args) -> int` (see `fightcard/cli.py`).
- Non-zero exit means: do not publish.
- `model pull` adopts a published model only if it passes every gate (sha256 of each file, `load_bundle`, the same `low_max` and `medium_max` as the running model, `examples.json` re-scored) and otherwise keeps the current one; it logs why and exits 0 either way, also when the bucket can't be reached. `FIGHTCARD_MODEL_URL` unset (and no `--pin`) is a no-op. `score --model-dir` is `state/model/current` once the pull has made it (`docs/SERVICE.md`).
- `history ingest` exits 1 without `listings/events_completed.html` in the cache (it says which events are UFC events), or when a completed bout has a method but no W/L outcome (it names the fight ids), and leaves the store unchanged; one transaction per run.
- `score` resolves the default `--history-db` against `--data`, not the cwd: `<--data>/../history/history.sqlite` (`data/` and `history/` side by side, as in the repo and on the VPS). An explicit `--history-db` is used as given.
- `score` exits 1 before writing anything when the history store is missing, unusable (empty, not SQLite, missing tables, no events) or stale: a completed event in events/ dated up to `--now` that the store doesn't have in full. In full means a store row with status `completed` and either no bouts (all cancelled) or at least one bout with an outcome. A completed row whose bouts have no outcome yet still counts as stale. It prints one line saying which. The one exception: an event `scrape`'s backfill skipped (in the store's `backfill_skips`, read-only; a store without that table, e.g. one made by `history ingest` before it existed, has none) is one WARNING line naming the event and the recorded reason, and doesn't change the exit code; the ratings leave it out. Each run logs the store's newest completed event and last page fetch against `--now`.
- `score --no-history` (exclusive with `--history-db`) scores tiers only: the history store isn't read and every fighter's rating fields stay as they are in fighters.json, and rankings.json is neither written nor removed.
- `scrape` reads the completed listing as `http://ufcstats.com/statistics/events/completed?page=all` (the whole listing, as `history ingest` reads it), also with `--no-history` (then only when `--recent` > 0).
- `scrape` keeps the history store current: it refetches the full completed listing every run and upserts it, every event page and every fighter page it fetched, through the same functions as `history ingest`, in one transaction. It takes the store's write lock before the first upsert (`BEGIN IMMEDIATE`, waiting up to 30 s for another writer) and commits the store **before** writing data/, so data/ is never ahead of the store (a store ahead of data/ is harmless: the next run rewrites data/). A failed fetch, check, lock or commit changes neither. If writing data/ fails after the commit, it exits 1 with "store updated, data/ may be partially written; the next run rewrites it". It resolves `--history-db` like `score` (default `<--data>/../history/history.sqlite`) and exits 1 before fetching anything when there's no store there; `--no-history` (exclusive with `--history-db`) scrapes without one. Like `history ingest`, it exits 1 with nothing written when a completed bout on this run's cards (upcoming, past-dated upcoming, the `--recent` completed ones) has a method but no W/L outcome. Seed the store once with `history ingest`; a refresh is then `scrape` → … → `score`.
- Backfill: completed events in data/ that the store doesn't have in full (as `score` checks) are refetched, oldest first, at most 20 a run (`BACKFILL_PER_RUN`; the rest in later runs), so `score`'s stale-store check passes once they're in. A backfill page that 404s or has a bout without an outcome is skipped with a warning naming the event: it isn't upserted and its data/ file stays as it was. The store's `backfill_skips` table (event id, last try, reason; scrape's bookkeeping, not dumped) keeps a skipped event from being retried for 7 days (`BACKFILL_RETRY`), so it doesn't take a slot of the 20 each run. It's removed once the event goes in. `score` warns on such an event instead of failing (see above). A backfilled card's fighters are fetched only when fighters.json lacks them.
- An event in the completed listing whose page has no bouts (all cancelled) is `completed`, in the store and in data/: its page alone would parse as `upcoming`, since there's no result on it. `history ingest` applies the same rule through the same `upsert_card`. The listing's next-upcoming row (next.png) doesn't count. A listed card with bouts keeps its parsed status, and becomes `completed` once a result is on its page.
- Upcoming events skipped for 0 fights go neither into data/ nor into the store.
- Known and accepted: a fighter whose fighters.json entry is fresh (fetched < 30 days ago) isn't refetched, so a fighter the store has no fighter page for (new since the seed) has only the card's name there, without bio or record, until their next refresh (within 30 days).
- `scrape` must exit non-zero if it finds 0 upcoming events, if the soonest upcoming event has 0 fights, or if all upcoming events together have 0 fights. Far-out events with no bouts announced yet are not written and stay out of `index.upcoming` until fights appear.
- `segments` exits 0 when ESPN is down or an event can't be matched, and keeps the segments it already has. It exits 1 only if data/ is unreadable.
- `score` writes `data/stale_fighters.json` (list of `{fighter_id, name, event_id}`), or removes it when nothing is stale. `scripts/stale_issue.py` turns it into the body of a "Re-export finish model" issue.
- `publish` exits 1 on invalid data or a failed write; the live generation stays (see Publishing above).
