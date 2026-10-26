# Running the engine as a service

On the VPS the engine is the `ufc-data` app in [infra](https://github.com/alexzfe/infra)'s `sites.yml`: one image,
`ghcr.io/alexzfe/fightcard-engine:<sha>`, that serves the [/v1/ API](API.md) and refreshes it on a schedule.

## One container

infra's `ansible/roles/apps/templates/compose.yml.j2` gives each app-env exactly one service (`app`) with at most
one volume, one health check and one memory cap. An nginx container plus a worker container sharing a volume would
need a second service in that template, so the engine runs as **one container** instead:

- PID 1 is `fightcard-service serve` (`src/fightcard/service/serve.py`, stdlib only). It starts nginx
  (`deploy/nginx/fightcard-engine.conf`, unchanged) in the foreground, runs the scheduler, and starts each pipeline
  run as a child process. If nginx exits, the supervisor exits and Docker restarts the container. `SIGTERM`
  (a deploy, `docker stop`) stops nginx gracefully and ends a running run.
- The base is `nginxinc/nginx-unprivileged` (Debian), pinned by tag and digest, with a uv-managed CPython and the
  locked venv copied in from a build stage, so the 762 MB of dependencies are installed at build time, never on the
  box. It runs as the image's `nginx` user (uid 101), never root.
- The Debian nginx image has `curl` but no `wget`, so `sites.yml` sets `healthcheck: image` and the image's own
  `HEALTHCHECK` curls `/healthz`.

## Schedule

The same rules as the old GitHub Actions refresh: **hourly at :17** and **daily at 06:00 UTC**. The daily run
always goes ahead; an hourly run only on an event's day or the day after. That decision is
`scripts/should_run.py`'s, unchanged: the runner calls it with `--event-name schedule --schedule "<cron>"`. Runs
started another way (`fightcard-service run` by hand, or the first start on an empty volume) skip the gate.

## A run

`fightcard-service run` (`src/fightcard/service/runner.py`):

```
gate → scrape → history → segments → images → score → publish
```

- Each stage is `fightcard --data /srv/fightcard/state/data <stage> …` in its own process, so a stage's memory is
  returned when it ends, and its output goes straight to stdout.
- **history** is `fightcard history ingest --html-dir state/html --db state/history/history.sqlite` (PR #2), and
  score reads that store (`--history-db`, the same path as its default `<--data>/../history/history.sqlite`). Score
  exits 1 when the store is missing, unusable or lacks a completed event, so a fresh volume needs the seed below.
  Ingest reads the ufcstats HTML cache under `state/html/`, which scrape will write once `feat/scrape-history`
  lands; until `state/html/listings/events_completed.html` exists the step logs
  `status=skipped reason=no-html-cache`. It reads local files only, so it runs with fetching off too.
- It stops at the first stage that exits non-zero. Every stage writes nothing on failure, and only publish switches
  what nginx serves, atomically (`current` symlink). So a failed run, at any stage, leaves the
  last good generation live; the next run starts from whatever data/ the successful stages left.
- A `flock` on `state/run.lock` means two runs never overlap: a run that can't take the lock logs `status=locked`
  and exits. The scheduler also never starts a run while its previous one is still going.
- Logs go to **stdout** (`docker logs`), one line per stage:
  `fightcard-run run=<id> step=score status=ok exit=0 duration_s=4.12`, plus a start and an end line per run.

## Volume

infra gives one volume, `ufc-data-<env>-data`, mounted at `/srv/fightcard`, which is also the publish root
(`fightcard publish --out /srv/fightcard`, and the nginx config's `alias /srv/fightcard/current/…`):

| Path | What |
|---|---|
| `current`, `generations/` | What nginx serves: the live generation and the previous few (`fightcard publish`) |
| `state/data/` | The working data/ the stages read and write |
| `state/cache/` | `XDG_CACHE_HOME`: the ufcstats `_fmc` cookie |
| `state/html/` | The ufcstats HTML cache `history ingest` reads (written by scrape once scrape-history lands) |
| `state/history/history.sqlite` | The SQLite history store (PR #2) |
| `state/run.lock` | The run lock |

nginx maps only `/v1/<documented resource>` to `current/`, so nothing under `state/` or `generations/` is reachable.

## Staging

Staging runs the same image with fetching off: no scrape, segments or images, only score and publish over the
data on its own volume. Prod and staging share the box's one IP, so a second scraper would double the load on
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
a tarball holding a top-level `data/` and `history/history.sqlite` (e.g. a store built by `fightcard history
ingest` from research's ufcstats cache, and prod's or the site repo's data/):

```sh
# Streamed in through exec, so the files are owned by the container user.
docker compose exec -T app sh -c 'mkdir -p /srv/fightcard/state && tar -xzf - -C /srv/fightcard/state' < seed.tar.gz
docker compose exec app fightcard-service run     # publish now instead of waiting for the next slot
```

The container test does exactly this. Nothing in the image or the release workflow seeds; it's run by hand.
