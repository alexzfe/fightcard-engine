# TDD evidence: browsers read /v1/ (CORS and a short event cache)

**Source plan:** the live-fight-night phase 3 brief ("let the browser read the /v1/ API"). There is no `*.plan.md`;
the journeys below were derived during this run.
**Branch:** `feat/v1-cors`. Infra needs no change (see "Why not infra" below).

## User journeys

1. As the site's live card (ufc.xlobax.com, ufc-staging.xlobax.com, a local dev server), I want to `fetch()`
   `/v1/index.json` and `/v1/events/<id>.json` from the browser, so results show up without a rebuild.
2. As the site polling about every 60 s on fight night, I want an event file to be at most about a minute old at
   Cloudflare and in the browser, so a result posted at ufcstats shows within about a minute of the engine
   publishing it.
3. As Cloudflare's cache, I want one entry per file whatever the page's Origin, so CORS doesn't split or poison
   the cache.
4. As the site's refresh workflow (`scripts/fetch_v1.py`), I want the index probe and the snapshot download to work
   as before.

## What was served before (prod, 2026-11-08 ~05:02Z)

| URL | Cache-Control | Access-Control-* | cf-cache-status / Age |
|---|---|---|---|
| `/v1/index.json` | `public, max-age=60` | none | MISS, then HIT with Age 29-30 |
| `/v1/events/110404b505fb562c.json` | `public, max-age=300` | none | MISS / REVALIDATED, then HIT |
| `/v1/snapshot.tar.gz` | `public, max-age=60` | none | MISS, then HIT with Age 30-31 |

## Task report

| Step | Command | Result |
|---|---|---|
| RED (`60b523b`) | `uv run pytest tests/deploy/test_nginx.py tests/test_docs.py` | `4 failed, 176 passed`: no `Access-Control-Allow-Origin "*" always;` in the config; CONTRACT.md has the event at 300 s, no `Access-Control-Allow-Origin: *`, still says "No CORS headers" |
| RED (`60b523b`) | `uv run pytest -m docker tests/deploy/test_nginx.py` | `42 failed, 5 passed`: 41 × `KeyError: 'access-control-allow-origin'`, 1 × `'public, max-age=300' == 'public, max-age=60'` (the event) |
| GREEN (`a7c714a`) | `uv run pytest` | `1132 passed, 1 skipped, 67 deselected` |
| GREEN (`a7c714a`) | `uv run pytest -m docker` | `59 passed` (the nginx tests and the image tests) |
| `fetch_v1.py` probe | the site's `scripts/fetch_v1.py` from `origin/main`, run against nginx-unprivileged with this config and a published tree: `python3 scripts/fetch_v1.py --base http://127.0.0.1:<port> --data <scratch>/data --now 2026-11-08T05:05:00Z` | `stale=false generated_at=2026-11-08T05:00:00Z age_hours=0.1`, data/ swapped in (events/e1.json, e2.json, fighters.json, schema.json, images), exit 0 |

## Test specification

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| 1 | Every /v1/ resource (index, events, fighters, schema, images, snapshot) says `Access-Control-Allow-Origin: *` | `test_nginx.py::test_resources_are_served_with_their_type_and_cache_policy` | docker | PASS |
| 2 | `/v1/events/<id>.json` is `public, max-age=60`; fighters/schema/rankings stay 300 s, images a day, snapshot 60 s | same; `test_config_sets_every_cache_policy` | docker / static | PASS |
| 3 | A 304 keeps the CORS header (the browser's revalidation stays readable) | `test_a_matching_etag_gets_304_with_the_cache_policy` | docker | PASS |
| 4 | 404 and 405 JSON errors carry it too, so a page can tell "gone" from a network error | `test_unknown_paths_and_directories_are_404`, `test_only_get_and_head_are_allowed` | docker | PASS |
| 5 | The response is the same for every Origin (prod site, staging site, localhost) and for none: `*`, no credentials, no `Vary: Origin` | `test_a_browser_origin_gets_the_same_cacheable_response` | docker | PASS |
| 6 | The config never echoes `$http_origin`, sends credentials or adds `Vary` | `test_config_allows_any_origin_without_credentials_or_vary_origin` | static | PASS |
| 7 | CONTRACT.md lists the event at `public, max-age=60` and documents `Access-Control-Allow-Origin: *`; "No CORS headers" is gone | `test_docs.py::test_contract_lists_every_endpoint_with_its_cache_policy`, `test_contract_covers`, `test_contract_no_longer_says_consumers_fetch_server_side_only` | static | PASS |

## Decisions

- `*`, not an origin list: the API is public, read-only and takes no credentials. A per-origin value would need
  `Vary: Origin`, which Cloudflare's cache handles poorly (it would serve one origin's header to another).
- No preflight support: `OPTIONS` stays a `405`. A plain `GET` without custom headers needs none, and the browser's
  own revalidation (`If-None-Match` from its HTTP cache) does not trigger one. API.md says to keep requests simple.
- Events move to 60 s like the index. Cloudflare already caches the JSON by the origin's max-age (Age stays under
  60 on HITs), and infra's `respect_origin_ttl` rule passes the same max-age to browsers. So a poll sees a copy at
  most about 60 s older than the origin's.

## Why not infra

The headers come from this repo's nginx (baked into the image); infra's edge nginx proxies them through untouched,
and its Cloudflare cache rule for ufc-data (`respect_origin_ttl`, infra #20) already gives browsers the origin's
Cache-Control. Nothing in `sites.yml` or `terraform/` needs to change.

## Coverage and known gaps

nginx config has no line coverage; the docker tests cover every location block. The Cloudflare side (HIT with the
CORS header after deploy) can only be checked live, after the release reaches prod.
