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
