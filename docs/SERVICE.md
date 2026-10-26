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
gate → scrape → segments → images → score → publish
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
  | score | 10 min |
  | publish | 10 min |

- `SIGTERM` to a run (the supervisor stopping) ends its current stage's process group
  the same way, then the run exits 143 with `status=terminated`.
- The **history store** is `state/history/history.sqlite`, beside `state/data/`: scrape upserts it in the same
  transaction as data/ (`feat/scrape-history`), and score reads it (`--history-db`). Both default to
  `<--data>/../history/history.sqlite`, and both exit 1 when it's missing, so a fresh volume needs the seed below.
  There is no separate history step.
- It stops at the first stage that exits non-zero. Every stage writes nothing on failure, and only publish switches
  what nginx serves, atomically (`current` symlink). So a failed run, at any stage, leaves the
  last good generation live; the next run starts from whatever data/ the successful stages left.
- A `flock` on `state/run.lock` means two runs never overlap: a run that can't take the lock logs `status=locked`
  and exits. Each stage inherits the locked file, so the lock is held until the stage is gone too, even if its run
  was killed. The scheduler also never starts a run while its previous one is still going. While it holds the lock,
  a run writes `state/run.json` (its pid and start time) for the health check.
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
| `state/history/history.sqlite` | The SQLite history store: scrape upserts it, score reads it |
| `state/run.lock`, `state/run.json` | The run lock, and the running run's pid and start time |

nginx maps only `/v1/<documented resource>` to `current/`, so nothing under `state/` or `generations/` is reachable.

## Health

`fightcard-service health` (`src/fightcard/service/health.py`) is the image's `HEALTHCHECK`, every 10 s; deploy-app
rolls a deploy back if it doesn't pass. It fails when:

- nginx doesn't answer `/healthz`;
- `current` exists but its `index.json` has no readable `generated_at`, or, where this env scrapes (prod), the
  `generated_at` is over **26 h** old (the daily run always scrapes; staging never does, so its age isn't checked);
- a live run's `state/run.json` says it started over **2 h** ago (above `RUN_TIMEOUT`, so only a run the supervisor
  failed to stop).

A volume with nothing published yet passes: the first deploy comes before seeding and must not be rolled back.
nginx's own `/healthz`, which infra's public smoke test hits, stays a plain 200 whatever the data.

## Backups

The history store is the one part of the volume a rescrape can't rebuild cheaply. `fightcard history backup --db
<path>` writes a consistent copy (SQLite's online backup, so a scrape writing meanwhile is fine) to stdout as a .tgz
holding `history/history.sqlite`, the seed tarball's layout. infra's `sites.yml` declares it for `ufc-data`
(`backup: [fightcard, history, backup, --db, /srv/fightcard/state/history/history.sqlite]`): `backup-app` runs it
in the prod container and the homeserver's `pull-backup@ufc-data.timer` pulls it nightly (14 daily, 8 weekly). To
restore, stream the archive into `state/` the way the seed goes in (below).

## Staging

Staging runs the same image with fetching off: no scrape, segments or images, only score and publish over the
data on its own volume, at :47 (above). Prod and staging share the box's one IP, so a second scraper would double the load on
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

The container test does exactly this. Nothing in the image or the release workflow seeds; it's run by hand.
