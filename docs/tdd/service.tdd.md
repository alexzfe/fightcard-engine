# TDD evidence: the engine as a VPS service (Phase 4)

Source: the orchestrator's Phase 4 brief (run fightcard-engine as a service: container, scheduler, infra entry).
The journeys below come from that brief. Branch `feat/service`, rebased onto `origin/main` at ff716bc (PR #2
merged). Pass counts in commit messages before 19fadf0 are from before the rebase. The final default suite is
572 passed (1e843cc/9e53d07 removed the history-step tests).

## User journeys

1. As the site owner, I want the /v1/ API served and refreshed by one container on the VPS, deployed like my other
   apps (`sites.yml`, `deploy-app`).
2. As the site owner, I want refreshes hourly at :17 and daily at 06:00 UTC, gated by `scripts/should_run.py`, never
   overlapping, and a failed refresh that keeps the last good generation live.
3. As the site owner, I want staging to run the same image without scraping ufcstats a second time.
4. As the site owner, I want PRs tested (including the container), main deployed to staging, and `v*` tags to prod.

## Task report

| Deliverable | RED | GREEN | Command | RED evidence | GREEN evidence |
|---|---|---|---|---|---|
| Design note `docs/SERVICE.md` | 0578e89 | c2f6f3b | `uv run pytest tests/test_docs.py` | 18 failed: `docs/SERVICE.md is missing` | 452 passed |
| Runner, schedule, supervisor | 5a5905c | 0668b07 | `uv run pytest tests/service` | collection: `No module named 'fightcard.service'` | 48/48; 500 total |
| Dockerfile, `.dockerignore`, container test | 51b3f48 | f346fd6 | `uv run pytest tests/deploy/test_image.py [-m docker]` | 8 failed `Dockerfile is missing`; `-m docker` 7 errors `open Dockerfile` | 8/8 static; `-m docker` 51/51 |
| release.yml + build.yml (replace ci.yml) | a78e1a9 | 8ce5219 | `ACTIONLINT=… uv run pytest tests/ops/test_workflows.py` | 18 failed: release.yml / build.yml missing, ci.yml present | 21/21; actionlint 1.7.12 + shellcheck 0.11.0 exit 0 |
| Refactor: shared logfmt | | 7d18457 | `uv run pytest` | | 522 passed |
| History step wired to PR #2's store (superseded by the next row) | 19fadf0 | 8544f9c | `uv run pytest tests/service`, `-m docker` | 13 failed; `-m docker`: `step=history status=skipped reason=no-html-cache` not in output | 577 passed; `-m docker` 51/51 |
| No separate history step (Phase 2b: scrape upserts the store) | 1e843cc | 9e53d07 | `uv run pytest tests/service`, `-m docker` | 1 failed: step lines still include `history` | 572 passed; `-m docker` 7/7 image |

## Test specification

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| 1 | Stages run in the order scrape → segments → images → score → publish, all over `state/data`, with no separate history step | `tests/service/test_runner.py::test_runs_every_stage_in_order`, `…share_the_state_data_dir…` | unit | PASS |
| 2 | Publish's `--out` is the nginx config's root (`/srv/fightcard`) | `test_publish_root_matches_the_nginx_config`, `test_image.py::test_the_publish_root_is_the_nginx_root` | unit/static | PASS |
| 3 | The first non-zero stage stops the run and its code is returned; a stage that can't start is 127 | `test_stops_at_the_first_failing_stage…[6]`, `test_a_stage_that_cannot_start…` | unit | PASS |
| 4 | One logfmt line per stage with exit code and duration, plus start/end lines | `test_one_line_per_stage…` | unit | PASS |
| 5 | Scheduled runs ask `should_run.py` with their cron; a skip runs nothing; a broken gate fails the run | `test_a_scheduled_run_asks_the_gate…`, `test_the_real_gate_script…` | unit/integration | PASS |
| 6 | A second run while the flock is held does nothing (exit 75); the lock is held during stages | `test_a_second_run_while_one_holds_the_lock…`, `test_the_lock_is_held…` | unit | PASS |
| 7 | Fetch off skips scrape/segments/images; prod-only fetch via `FIGHTCARD_HOST`/`FIGHTCARD_FETCH_HOST` | `test_fetch_off…`, `test_fetch_comes_from_the_environment[6]` | unit | PASS |
| 8 | The history store is `state/history/history.sqlite`, beside data/ (scrape's and score's default), and its dir exists before stages run | `test_scrape_and_score_share_the_history_store_beside_data` | unit | PASS |
| 9 | Slots: hourly :17 and daily 06:00 UTC, strictly after now; DAILY_CRON matches should_run.py | `tests/service/test_schedule.py` | unit | PASS |
| 10 | Supervisor: nginx in foreground, a run per slot, startup run on an empty root, never two runs, SIGTERM → run TERM + nginx QUIT, nginx exit → exit 1 | `tests/service/test_serve.py` | unit | PASS |
| 11 | Image: pinned bases, multi-stage, `uv sync --frozen --no-dev --no-editable`, non-root, the repo nginx conf, curl HEALTHCHECK, supervisor PID 1, STOPSIGNAL SIGTERM, whitelist .dockerignore | `tests/deploy/test_image.py` static | static | PASS |
| 12 | Container: `/healthz` 200 and Docker healthy; the startup run on an empty volume fails without publishing; seeding + `fightcard-service run` publishes `/v1/`; a failed run keeps the last good generation; every process is uid 101; `memory.peak` < 768 MiB; `docker stop` exits 0 promptly | `tests/deploy/test_image.py` `-m docker` | e2e (docker) | PASS (peak 193 MiB, stop 0.5s) |
| 13 | Workflows: triggers, `permissions: {}`, per-job grants, test + docker jobs, build after both, staging/prod via deploy-app for `ufc-data`, release after prod, SHA pins, no persisted credentials | `tests/ops/test_workflows.py` | static | PASS |

## Coverage and gaps

`uv run pytest --cov=fightcard.service`: 97% (runner 100%, schedule 100%, serve 95%, `__main__` 89%). Not covered:
the `serve` subcommand dispatch, `__main__`'s `sys.exit`, and serve's kill-after-timeout branch.

Gaps: score's memory was measured on the publish fixture only (peak 193 MiB), not real data. The scrapers aren't run
in the container (fetch off). Scrape only upserts the history store once `feat/scrape-history` merges; until then this branch's scrape leaves the store as seeded.

## Review fixes (PR #4 code review)

Source: the orchestrator's p4-review-fixes brief (findings 1-10 on `feat/service`, and infra PR #17 `add-ufc-data`).
Journeys added: as the site owner, I want a hung stage to fail its run instead of blocking every refresh; a busy
slot (above all the daily one) to run when the run ends; a deploy's stop to fit Docker's 10 s with nothing orphaned;
a container that reports unhealthy when it serves stale data or a run is stuck; a nightly backup of the history
store; and staging never scoring at the same time as prod.

Real-process tests (finding 10) use `tests/service/fake_stage.py` as the `fightcard` binary: it sleeps, ignores
SIGTERM, or starts a grandchild when told to, and reports its pid, process group, niceness and the lock.

| # | Finding | RED | GREEN | RED evidence | GREEN evidence |
|---|---|---|---|---|---|
| 1 | No per-stage timeout | 194615a | e03866e | 18 failed: no `runner.TIMEOUTS`; `execute()` takes no timeout; stage pgid == runner's | 577 passed |
| 4 | SIGTERM orphaned the stage; lock freed while it ran; no reaping | 07b128c | c2aa615 | 3 failed: run outlived by its stage (pipe held 10 s), lock free while scrape ran, zombie under serve | 580 passed |
| 3 (+1's run cap) | Shutdown budget 20 s + 20 s; no whole-run cap | 1107835 | ddf0f72 | 6 failed (the message says 7): shutdown > 20 s with nginx ignoring QUIT and the stage ignoring TERM; cap never fired | 586 passed; worst-case stop 5.9 s |
| 2 | Busy ticks dropped | 4dddde4 | 6ee2091 | 6 failed: busy slots dropped | 591 passed; the real test was re-run against the old serve.py: 1 failed |
| 7 | Both envs score at :17 | d5d89e5 | 391f119 | 14 failed: no `schedule_minute`, `next_fire` takes no minute | 605 passed |
| 5 | Healthy while serving nothing useful | 72fc03f | 91876eb | collection error: no `fightcard.service.health`; Dockerfile still curls /healthz | 617 passed; `-m docker` below |
| 6 | No backup of the history store | 1634dbf | ae06275 | 4 failed: `invalid choice: 'backup'` | 621 passed; infra 1120daf |
| 8 (+ docs for 1-7) | Restart with stale data waits silently | b099863 | bdb9786 | 13 failed doc terms | 636 passed |
| 9 | infra comment wording | (scratchpad check) | infra 1120daf | `check_sites.py`: `backup: None` | `ok` |

Decisions:

- **#1**: `TIMEOUTS` are in `runner.py`, with `KILL_GRACE` 3 s and exit 124 (timeout(1)'s code). Stages run with
  `start_new_session=True` and `nice` 10 via `preexec_fn`, which is safe because the run process has no threads.
  `RUN_TIMEOUT` = sum of the stage timeouts + a `KILL_GRACE` each + 10 min = 5415 s.
- **#2**: one pending slot. A queued daily slot drops later hourlies, and a daily slot replaces a queued hourly one.
- **#3**: nginx `SIGQUIT` and the run group's `SIGTERM` go out together, then a 5 s `STOP_GRACE`, then `SIGKILL`.
  The run needs at most `KILL_GRACE` (3 s) to end its stage.
- **#4**: the run's SIGTERM handler raises `Terminated` (a `BaseException`), so `execute()` ends the stage group on
  the way out. A second TERM is ignored. The stage gets the locked fd through `pass_fds`. `serve.reap()` uses
  `waitid(WNOWAIT)` to peek, so nginx's and the run's exit codes stay with their Popen objects.
- **#5**: a separate `fightcard-service health` is cleaner than changing nginx's `/healthz`, which the public smoke
  test and the first, unseeded deploy depend on. `generated_at` only advances when scrape runs, so the 26 h check
  applies only where fetching is on (prod). Otherwise staging would go unhealthy a day after seeding and every
  staging deploy would roll back. The busy check reads `state/run.json` (pid + start), not the flock: probing the
  flock could make a starting run see `locked`.
- **#7**: the minute comes from `FIGHTCARD_SCHEDULE_MINUTE`, else from the host: 17 on the fetch host, 47 elsewhere,
  so `sites.yml` needs no change. The daily slot moves by the same amount (staging 06:30) and is still reported as
  `DAILY_CRON`.
- **#8**: documented, not changed. A startup run whenever data is stale would scrape on every prod deploy.

### Test specification (review fixes)

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| R1 | Timeouts are per stage, in one place (30/15/15/10/10 min) and passed to each stage | `test_supervision.py::test_the_timeouts_are_per_stage…`, `test_runner.py::test_each_stage_gets_its_own_timeout` | unit | PASS |
| R2 | A stage past its timeout is killed with its whole process group (grandchild too); `status=timeout`, exit 124 | `test_a_stage_past_its_timeout_is_killed_with_its_process_group` | real process | PASS |
| R3 | A stage ignoring SIGTERM is SIGKILLed after `KILL_GRACE` | `test_a_stage_that_ignores_sigterm_is_killed_after_the_grace` | real process | PASS |
| R4 | Each stage leads its own process group at nice 10 | `test_each_stage_runs_in_its_own_process_group_at_nice_10` | real process | PASS |
| R5 | SIGTERM to a run ends its stage (and grandchild) before the run exits 143; the lock is free after | `test_sigterm_to_a_run_ends_its_stage_first_then_the_run` | real process | PASS |
| R6 | A killed run can't release the lock while its stage still runs | `test_a_stage_holds_the_lock_even_if_its_run_is_killed` | real process | PASS |
| R7 | serve (as subreaper standing in for PID 1) leaves no zombie after a killed run | `test_serve_reaps_the_orphans_of_a_killed_run` | real process | PASS |
| R8 | Stop with nginx ignoring QUIT and a stage ignoring TERM ends in < 8 s with nothing left | `test_shutdown_with_a_stage_mid_run_fits_in_dockers_10s` | real process | PASS (5.9 s) |
| R9 | serve stops a run past `RUN_TIMEOUT`, killing its stage | `test_serve_stops_a_run_past_the_whole_run_cap`, `test_serve.py::test_a_run_past_the_cap…` | real process / unit | PASS |
| R10 | `RUN_TIMEOUT` covers every stage and stays under the 2 h busy limit; `KILL_GRACE` < `STOP_GRACE` ≤ 7 | `test_serve.py::test_the_run_cap_covers…`, `test_the_shutdown_budget…` | unit | PASS |
| R11 | A busy slot is queued and starts when the run ends; at most one waits; a daily wins | `test_serve.py::test_a_busy_slot…`, `test_a_pending_daily_slot_wins…[2]`, `test_at_most_one_slot_waits`, `test_supervision.py::test_a_slot_that_fires_mid_run…` | unit / real process | PASS |
| R12 | Staging (off the fetch host) fires at :47 and 06:30; `FIGHTCARD_SCHEDULE_MINUTE` overrides; bad values are refused | `test_schedule.py::test_next_fire_at_another_minute[4]`, `test_runner.py::test_the_schedule_minute…[5]`, `test_a_bad_schedule_minute…[3]`, `test_serve.py::test_staging_runs_at_its_own_minute` | unit | PASS |
| R13 | Health: unseeded passes; nginx down, stale (> 26 h, fetch on only), unreadable index, busy > 2 h (live pid) fail | `tests/service/test_health.py` | unit (real files, local HTTP) | PASS |
| R14 | Image HEALTHCHECK is `fightcard-service health`; healthy after publishing; exit 1 on stale data with fetch on | `test_image.py` static + `-m docker` | static / e2e | PASS |
| R15 | `fightcard history backup` streams a .tgz of `history/history.sqlite`, consistent mid-transaction, restorable to `state/`; missing store exits 1 without creating it | `tests/history/test_history_backup.py` | integration | PASS |
| R16 | infra's backup command works in the image (`docker exec … fightcard history backup --db …`) | `test_image.py::test_the_history_backup_streams_a_tgz_the_way_infra_runs_it` | e2e (docker) | PASS |
| R17 | SERVICE.md covers all of the above; its timeout table matches `runner.TIMEOUTS` | `test_docs.py::test_service_doc_covers_the_supervision[14]`, `test_service_doc_states_the_stage_timeouts` | static | PASS |

### Verification (final)

- `uv run pytest -q`: **636 passed, 1 skipped, 60 deselected**.
- `uv run pytest -m docker -v tests/deploy`: **54 passed** (image + nginx). Image 1501 MiB, `memory.peak` 203 MiB of
  768 MiB, `docker stop` 0.5 s, exit 0.
- Coverage (`--cov=fightcard.service --cov=fightcard.history.stage`, in-process only): 96%. stage 100%, schedule
  100%, runner 96%, health 96%, serve 93%, `__main__` 92%. The code the real-process tests exercise runs in child
  processes and isn't counted.
- actionlint 1.7.12 clean, run as `mise exec shellcheck@0.11.0 -- uvx --from actionlint-py==1.7.12.25 actionlint`,
  because the local shellcheck shim has no version.
- infra: `terraform fmt -check` OK, `terraform validate` OK, `ansible-lint site.yml` 0 failures.

## Review fixes round 2

Source: the orchestrator's decisions on the round 1 report ("Decisions that may want the owner's opinion"), plus
one doc request after PR #3 merged. Commit SHAs are after the rebase onto `origin/main` (`aaaefa7`); before the
rebase, #1's RED/GREEN were `c1f9107`/`3dffeeb`.

| # | Change | RED | GREEN | RED evidence | GREEN evidence |
|---|---|---|---|---|---|
| 1 | The HEALTHCHECK fails only on nginx not answering `/healthz` or a live run busy > 2 h. Stale or unreadable data no longer fails it, because deploy-app would roll back every deploy while ufcstats is down. `health --staleness` prints `generated_at` and its age, and exits 1 when `current` exists and is > 26 h old or unreadable (not in the HEALTHCHECK) | `9b850f9` | `82ee48e` | `pytest tests/service/test_health.py tests/test_docs.py`: 9 failed (no `health.staleness`, `--staleness` rejected with exit 2, stale/unreadable data → problems), 78 passed; `-m docker -k stale`: 1 failed | 640 passed; `-m docker` 54/54; actionlint 0 |
| 2 | SERVICE.md's seeding step: seed the store fully with `fightcard history ingest` before the first run, because scrape backfills at most 20 (`BACKFILL_PER_RUN`) old events a run and score exits 1 on each missing one (`backfill_skips` aside) until it's in | `9559185` | `fc93f64` | `pytest tests/test_docs.py`: 1 failed (`before the first run` missing), 72 passed | 696 passed |

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| S1 | Stale (30 days old) or unreadable data leaves `health` passing, with fetch on or off | `test_health.py::test_stale_or_unreadable_data_does_not_fail_the_healthcheck[2]` | unit | PASS |
| S2 | `staleness`: 25 h 59 min passes; 26 h 01 min fails and states `age 26 h 01 min`; unseeded passes; unreadable index fails | `test_staleness_*` (4) | unit | PASS |
| S3 | CLI `health`: 0 when stale, 1 when a run is busy > 2 h, 1 when nginx is down | `test_cli_health_exits_0_or_1` | unit | PASS |
| S4 | CLI `health --staleness`: 0 unseeded or fresh (prints the age), 1 on 2020 data, without asking nginx | `test_cli_health_staleness_exits_1_on_stale_data_and_prints_the_age` | unit | PASS |
| S5 | In the image, stale data with `FIGHTCARD_FETCH=on` keeps `health` at 0; `health --staleness` exits 1 | `test_image.py::test_stale_data_keeps_the_healthcheck_healthy_and_staleness_reports_it` | e2e (docker) | PASS |
| S6 | SERVICE.md mentions `--staleness`; its seeding section says to seed before the first run and states the backfill cap and `backfill_skips` | `test_docs.py` | static | PASS |

Infra (decision 2, not TDD: config only). `add-ufc-data` `fed27d4` removes ufc-data's `backup:` line, lists Backups
as Settle again, and marks ufc-data's timer and restore notes as applying once the line lands. The new branch
`ufc-data-backup` (worktree `~/Projects/infra-ufc-data-backup`, one commit `a9440f6` on top of `fed27d4`) puts it
back. Its `sites.yml` and `README.md` are byte-identical to `1120daf`. Merge it once the engine with `fightcard
history backup` is on prod. Both branches pass `terraform fmt -check`, `terraform validate` and `ansible-lint` (0
failures).

Rebase (decision 3). `origin/main` moved to `aaaefa7` (PR #3) during this round, so `feat/service` was rebased onto
it. There was one conflict, in README.md's quick-start block: kept main's `history ingest` seed and `scrape` keeps
the store current lines, plus this branch's `-m docker` comment. The pre-rebase head is kept as
`backup/feat-service-pre-rebase-r2`.

### Verification (after the rebase)

- `uv run pytest -q`: **696 passed, 1 skipped** (the actionlint test without `ACTIONLINT` set), 61 deselected.
- `uv run pytest -q -m docker`: **54 passed**. `uv run pytest -q -m seed`: **6 passed**.
- actionlint 1.7.12: exit 0 (`mise exec shellcheck@0.11.0 -- uvx --from actionlint-py==1.7.12.25 actionlint`).
- Coverage `pytest tests/service --cov=fightcard.service`: 95% total, health 97%.
