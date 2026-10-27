# Running the engine as a service

On the VPS the engine is the `ufc-data` app in [infra](https://github.com/alexzfe/infra)'s `sites.yml`: one image,
`ghcr.io/alexzfe/fightcard-engine:<sha>`, that serves the [/v1/ API](API.md) and refreshes it on a schedule.

## One container

infra's `ansible/roles/apps/templates/compose.yml.j2` gives each app-env exactly one service (`app`) with at most
one volume, one health check and one memory cap. An nginx container plus a worker container sharing a volume would
need a second service in that template, so the engine runs as **one container** instead:

- PID 1 is `fightcard-service serve` (`src/fightcard/service/serve.py`, stdlib only). It starts nginx
  (`deploy/nginx/fightcard-engine.conf`, unchanged) in the foreground, runs the scheduler, and starts each pipeline
  run as a child process leading its own process group. If nginx exits, the supervisor exits and Docker restarts
  the container. compose gives the app no `init:`, so serve also reaps any orphan a killed run or stage leaves.
- `SIGTERM` (a deploy, `docker stop`) sends nginx its graceful `SIGQUIT` and the run's process group `SIGTERM` at
  the same time, waits `STOP_GRACE` (5 s) for both, then `SIGKILL`s whatever is left: the whole stop takes at most
  about 6 s, inside Docker's 10 s. The run, on its `SIGTERM`, ends its stage first (below), within 3 s.
- The base is `nginxinc/nginx-unprivileged` (Debian), pinned by tag and digest, with a uv-managed CPython and the
  locked venv copied in from a build stage, so the 762 MB of dependencies are installed at build time, never on the
  box. It runs as the image's `nginx` user (uid 101), never root.
- The Debian nginx image has no `wget`, so `sites.yml` sets `healthcheck: image` and the image's own `HEALTHCHECK`
  runs `fightcard-service health` ([Health](#health)).

## Schedule

The same rules as the old GitHub Actions refresh: **hourly at :17** and **daily at 06:00 UTC**. The daily run
always goes ahead; an hourly run only on an event's day or the day after. That decision is
`scripts/should_run.py`'s, unchanged: the runner calls it with `--event-name schedule --schedule "<cron>"`. Runs
started another way (`fightcard-service run` by hand, or the first start on an empty volume) skip the gate.

- **Staging fires half an hour later**: hourly at **:47** and daily at 06:30 (still reported to the gate as the
  daily cron), so the two envs never score at once on the box's 2 vCPUs. The minute comes from the host: 17 where
  `FIGHTCARD_HOST` is `FIGHTCARD_FETCH_HOST` (prod), 47 elsewhere; `FIGHTCARD_SCHEDULE_MINUTE` overrides it. Stages
  also run at `nice` 10, so nginx keeps the CPU it needs.
- **A slot that fires during a run waits for it** (`status=queued`) and starts as soon as it ends. At most one waits;
  a queued daily slot, which the gate always lets through, wins over a later hourly one (`status=dropped`).
- **A restart doesn't run straight away** unless nothing is published yet: after a deploy or a crash, the data
  served is the last published generation until the next slot (at most an hour away). To refresh now, run
  `docker compose exec app fightcard-service run`.

## A run

`fightcard-service run` (`src/fightcard/service/runner.py`):

```
gate → scrape → segments → images → model pull → score → publish
```

- Each stage is `fightcard --data /srv/fightcard/state/data <stage> …` in its own process, so a stage's memory is
  returned when it ends, and its output goes straight to stdout. It runs in its own session, so its **process
  group** holds it and anything it starts, at `nice` 10.
- Each stage has a **timeout** (`runner.TIMEOUTS`), far past its normal duration so only a hang hits it. Past it,
  the stage's process group gets `SIGTERM`, 3 s (`KILL_GRACE`), then `SIGKILL`; the run logs `status=timeout` and
  fails with exit 124. The supervisor also stops a whole run after `RUN_TIMEOUT` (every stage at its limit, plus
  10 min: about 90 min) as a backstop, logging `fightcard-serve status=timeout`.

  | Stage | Timeout |
  |---|---|
  | scrape | 30 min |
  | segments | 15 min |
  | images | 15 min |
  | model | 5 min |
  | score | 10 min |
  | publish | 10 min |

- `SIGTERM` to a run (the supervisor stopping) ends its current stage's process group
  the same way, then the run exits 143 with `status=terminated`.
- The **history store** is `state/history/history.sqlite`, beside `state/data/`: scrape upserts it in the same
  transaction as data/ (`feat/scrape-history`), score reads it (`--history-db`), and so does images, only to work out
  the P4P fighters' headshots. All three default to `<--data>/../history/history.sqlite`. scrape and score exit 1 when
  it's missing, so a fresh volume needs the seed below; images warns and fetches the card fighters only.
  There is no separate history step.
- It stops at the first stage that exits non-zero, except the model pull, which is logged and skipped past ([The
  model](#the-model)). Every stage writes nothing on failure, and only publish switches
  what nginx serves, atomically (`current` symlink). So a failed run, at any stage, leaves the
  last good generation live; the next run starts from whatever data/ the successful stages left.
- A `flock` on `state/run.lock` means two runs never overlap: a run that can't take the lock logs `status=locked`
  and exits. Each stage inherits the locked file, so the lock is held until the stage is gone too, even if its run
  was killed. The scheduler also never starts a run while its previous one is still going. While it holds the lock,
  a run writes `state/run.json` (its pid and start time) for the health check.
- Logs go to **stdout** (`docker logs`), one line per stage:
  `fightcard-run run=<id> step=score status=ok exit=0 duration_s=4.12`, plus a start and an end line per run.

## The model

The finish model in `model/` ([model/README.md](../model/README.md)) is baked into the image, and the image is
the fallback. With `FIGHTCARD_MODEL_URL` set the service also pulls the model research publishes, so a new export
reaches the box without a release. `fightcard model pull` (`src/fightcard/model/`) is the `model` step of every
run, right before score. It runs where fetching is off too (staging), since it isn't a ufcstats fetch.

**The bucket.** `FIGHTCARD_MODEL_URL` is a public-read bucket (Cloudflare R2, `https://models.xlobax.com/`), so
there is no credential on the VPS. It holds `<version>/<file>` for each bundle (the files `python -m export` writes;
a version's prefix is immutable, and a `+` in it is fetched as `%2B`), and `latest.json`, written last, after every
file of its version is up:

```json
{"format_version": 1, "version": "2026-10-14+5006318a", "as_of": "2026-10-14", "files": {"model.txt": "<sha256>", "...": "..."}}
```

**A pull.**

1. Fetch `latest.json`. If its `version` is the one `current` points at, that's all: `up to date`.
2. Otherwise download each file into a staging dir under `state/model/`. `latest.json` is checked before anything is
   fetched: `format_version` 1, a plain version name, an `as_of` date, only the bundle's file names (an unknown or
   path-like name is rejected), a sha256 for each, and every required file listed. Redirects are followed only to
   http(s) URLs. An `as_of` older than the running model's is held too (no silent downgrade; going back is the pin,
   below); the same date is fine.
3. The candidate then has to pass every gate, in this order: the **sha256** of each file; `manifest.json` names the
   same version; `load_bundle` loads it; `thresholds.json`'s `low_max` and `medium_max` **equal the running model's**;
   and `examples.json` re-scores to its own `p_finish` and tier (the parity check of `tests/score/test_score_parity.py`;
   anything malformed in it, or that raises while scoring, is a hold, not a crash). `manifest.json`'s `as_of` is
   held to the same not-older rule. A new cut point changes what Low, Medium and High mean, so it needs a person: ship it by hand ([model/README.md](../model/README.md)).
4. It's renamed to `state/model/<version>/` and `state/model/current` (a relative symlink, like publish's) is
   switched to it by an atomic rename, so score sees the old model or the new one. The previous version stays kept,
   and only the last 3 versions are (oldest pruned first; never the one `current` names).

**Held, or unreachable: the current model stays.** A version that fails a gate is *held*: the log says
`model pull: held <version>: <reason>; keeping <current>`. Before the gate runs, the pull writes
`state/model/.held-<version>` (the file digests, the running version and why); a gate verdict, or a pull that dies
inside the gate (LightGBM can end the process on a corrupt file), leaves it there. While `latest.json` names the same
files and the same model is running, the version is not fetched again, and the same `held` line is logged every run.
It is tried again when the digests or the running version change, and the marker goes when `latest.json` moves on or
the version is adopted (delete the file to force a retry). A checksum mismatch leaves no marker: it says nothing
about the model, so the next run fetches again. A bucket that is down, answers 404, returns something unreadable, cuts off or sends a file over
256 MiB is *unreachable*: `model pull: failed, keeping <current>`, no verdict on any model. Either way the pull
ends with exit 0, and the runner goes on even if the pull crashes or hangs (its 5 min timeout), so a pull does not fail the
run: score runs on the model it had. Look for `step=model` in the run's lines and `model pull:` in the output.

**No-op, and the first boot.** With `FIGHTCARD_MODEL_URL` unset (and no pin) the pull is a no-op that logs one
line and creates nothing: `state/model` doesn't exist, and score reads `FIGHTCARD_MODEL_DIR` (`/app/model`, the
baked model), also if an earlier configuration left a `state/model/current` behind. The first boot with a url or a pin
copies the baked model to `state/model/<its version>/` and points `current` at it, so there is always a model to
compare a new one with and to roll back to. Score reads `state/model/current` only when a pull is configured (a url
or a pin) and there is one; the runner passes the baked model as `--fallback-model-dir`, which score uses, with an
`ERROR` line, if `current` won't load. A `current` that no longer loads is seeded again from the baked model.

**A new image.** When the image's baked model is newer (a later `as_of` than `current`'s), every pull adopts it (the gates
above, except the cut-point check: the image defines the cut points, so bundles with the new ones aren't held
forever), and under a pin it is only kept, so it can be pinned. A baked model that is not newer but differs from
`current` is logged each run: `baked model <v> … is not newer than current <v>`.

**Which model scored.** score logs `score: model <version> (as_of …) from <dir>`, and `/v1/index.json` carries `model.version`, `model.as_of`
and `model.stale_fighters` (the number of fighters whose record moved since the export, whose bouts get no tier).
Research's export gate reads them: it exports when the box already runs the newest published version and
`model.stale_fighters` is above 0 ([CONTRACT.md](CONTRACT.md)).

**Pin and rollback.** `FIGHTCARD_MODEL_PIN=<version>` makes the pull switch `current` to that kept version, without
fetching anything, and keep it there: newer versions in the bucket wait until the pin is removed. A pin can only
name a kept version (the last 3) that still loads; anything else logs an `ERROR FIGHTCARD_MODEL_PIN … ` line on
every run and the current model stays. Pinning is the only way to roll back, because a `latest.json` older than the
running model is held. To roll back:

```sh
docker compose exec app ls /srv/fightcard/state/model     # the kept versions, oldest first
docker compose exec -e FIGHTCARD_MODEL_PIN=<version> app fightcard-service run     # this run; scores with it
```

For it to last, set `FIGHTCARD_MODEL_PIN: <version>` in the app's `env` in infra's `sites.yml` and deploy; remove it
to resume pulling. Pulling an older export would be a new `as_of`, so it is the pin, not `latest.json`, that goes back.

A pull sweeps `state/model/.tmp-*` (staging) and `.current-*` (a switch that was killed half way) before it starts.

## Volume

infra gives one volume, `ufc-data-<env>-data`, mounted at `/srv/fightcard`, which is also the publish root
(`fightcard publish --out /srv/fightcard`, and the nginx config's `alias /srv/fightcard/current/…`):

| Path | What |
|---|---|
| `current`, `generations/` | What nginx serves: the live generation and the previous few (`fightcard publish`) |
| `state/data/` | The working data/ the stages read and write |
| `state/cache/` | `XDG_CACHE_HOME`: the ufcstats `_fmc` cookie |
| `state/history/history.sqlite` | The SQLite history store: scrape upserts it, score reads it |
| `state/model/` | The pulled model: `<version>/` for the last 3 versions and `current` -> the one score reads ([The model](#the-model)); absent until `FIGHTCARD_MODEL_URL` is set |
| `state/run.lock`, `state/run.json` | The run lock, and the running run's pid and start time |

nginx maps only `/v1/<documented resource>` to `current/`, so nothing under `state/` or `generations/` is reachable.

## Health

`fightcard-service health` (`src/fightcard/service/health.py`) is the image's `HEALTHCHECK`, every 10 s; deploy-app
rolls a deploy back if it doesn't pass. It fails only when:

- nginx doesn't answer `/healthz`;
- a live run's `state/run.json` says it started over **2 h** ago (above `RUN_TIMEOUT`, so only a run the supervisor
  failed to stop).

It ignores the data's age. A container that went unhealthy because ufcstats had been down for a day would make
deploy-app roll back every deploy, so no fix could ship until an external site recovered. A volume with nothing
published yet passes too: the first deploy comes before seeding.

`fightcard-service health --staleness` reports the age instead, for alerting (nothing runs it yet). It prints the
`generated_at` and its age, and exits 1 when `current` exists and its `generated_at` is over **26 h** old (the daily
run always scrapes, plus slack for it to finish) or unreadable. Only prod scrapes, so on staging it always fails a
day after seeding; alert on prod's.

nginx's own `/healthz`, which infra's public smoke test hits, stays a plain 200 whatever the data.

## Backups

The history store is the one part of the volume a rescrape can't rebuild cheaply. `fightcard history backup --db
<path>` writes a consistent copy (SQLite's online backup, so a scrape writing meanwhile is fine) to stdout as a .tgz
holding `history/history.sqlite`, the seed tarball's layout. infra's `sites.yml` declares it for `ufc-data`
(`backup: [fightcard, history, backup, --db, /srv/fightcard/state/history/history.sqlite]`): `backup-app` runs it
in the prod container and the homeserver's `pull-backup@ufc-data.timer` pulls it nightly (14 daily, 8 weekly). To
restore, stream the archive into `state/` the way the seed goes in (below).

## Release order

The first service run of an engine with `rankings.json` writes it (score) and puts it in `/v1/snapshot.tar.gz`
(publish). The site's `scripts/fetch_v1.py` then allowlisted the top-level files it accepted and refused any other
member (`unsafe snapshot member 'rankings.json': not part of the /v1/ layout`). So:

1. Merge the site's change that adds `rankings.json` to `fetch_v1` (site branch `feat/rankings`) to the site's `main`
   and let it deploy, before this engine is deployed.
2. Then deploy the engine.

Deployed the other way round, every v1 refresh fails in `DATA_SOURCE=v1` mode (exit 1, not the stale code 3, so the
hourly runs fail too and the site's data/ freezes); in `pipeline` mode only the `shadow` job breaks. A rollback is
safe in either order: the previous engine doesn't publish the file (contract `"1.1"`) and the site treats it as optional.
That order was for `rankings.json` only. Since site PR alexzfe/fight-card-website#12, `fetch_v1` skips files
outside the layout it knows instead of refusing the snapshot, so a file added to `/v1/` later can be deployed before
any site change (docs/CONTRACT.md, Versioning). This holds once site PR #12 is on the site's `main`.

## Staging

Staging runs the same image with fetching off: no scrape, segments or images, only the model pull, score and
publish over the data on its own volume, at :47 (above). Prod and staging share the box's one IP, so a second scraper would double the load on
ufcstats and risk getting prod's IP blocked; the scrapers are covered by the fixture tests and `-m live`.
`sites.yml`'s `env` is the same for both envs, so it passes `FIGHTCARD_HOST: "{host}"` and
`FIGHTCARD_FETCH_HOST: ufc-data.xlobax.com`; fetching is on only where the two match. `FIGHTCARD_FETCH=off` turns
it off outright (the container test uses that).

## Memory

`mem_limit: 1g` for prod and `staging_mem_limit: 768m`. The heaviest stage is score (pandas + LightGBM): about
250 MB peak on the test fixture. The container test checks the cgroup's peak stays under 768m for a whole run;
watch `docker stats` after the first real runs and lower the caps if there's room.

## Seeding a new volume (manual, once per env)

A fresh volume has no data/ and no history store, and score refuses to run without the store. On the first
start, with nothing published yet, the supervisor starts a run straight away; it fails at score (prod scrapes
first) and nothing is published until the volume is seeded. To seed, on the VPS (`/opt/apps/ufc-data-<env>`), with
a tarball holding a top-level `data/` and `history/history.sqlite` (the store built once by
`fightcard history ingest` from research's ufcstats cache, and prod's or the site repo's data/):

```sh
# Streamed in through exec, so the files are owned by the container user.
docker compose exec -T app sh -c 'mkdir -p /srv/fightcard/state && tar -xzf - -C /srv/fightcard/state' < seed.tar.gz
docker compose exec app fightcard-service run     # publish now instead of waiting for the next slot
```

Seed the store fully with `fightcard history ingest` before the first run, from a cache that covers every completed
event in the seed's `data/`. Scrape backfills the store's missing completed events itself, but at most 20 a run
(`BACKFILL_PER_RUN`, oldest first, [CONTRACT.md](CONTRACT.md)), and until each one is in, score exits 1 on it as a
stale store (the only exception is an event the backfill skipped, which `backfill_skips` records and score warns
on). A store that's hundreds of events short fails run after run, publishing nothing new, until the backfill
catches up.

The container test does exactly this. Nothing in the image or the release workflow seeds; it's run by hand.
