# TDD evidence: ops (packaging, CI, deploy)

**Source plan:** `PLAN.md` ("Hosting & deploy", milestone 7) and the ops brief. There is no `*.plan.md` file; the
journeys below were derived during this run.
**Branch:** `feat/v1`. Infra changes are on `add-ufc-site` in the worktree `~/Projects/infra-ufc`, not pushed.
**Operational doc:** `docs/DEPLOY.md`.

## User journeys

1. As the site owner, I want data refreshed daily, and hourly around fight nights, without burning Actions minutes on
   quiet days.
2. As the site owner, I want a failed scrape, image or score stage to stop everything, so a bad parse never reaches
   prod.
3. As the site owner, I want one GitHub issue telling me when the finish model is stale, and which fighters are
   affected.
4. As the site owner, I want merges to main on staging, tags on prod, and data refreshes on prod automatically.
5. As the VPS, I want an image that serves on :8080 and answers the health path that infra's compose healthcheck
   probes.

## Task report

| Task | Summary | Validation run | RED | GREEN |
|---|---|---|---|---|
| `scripts/should_run.py` | Gate: daily and manual runs always go ahead; hourly runs only on an event's date or the day after; a missing index means bootstrap (run); a corrupt index raises. | `uv run --project pipeline pytest scripts/tests` | `ModuleNotFoundError: No module named 'should_run'` (9a8c132) | 13/13 pass (5835854) |
| `scripts/stale_issue.py` | Builds the issue body (marker, count, model date, fighter links, event names, re-export steps). Writes `stale`/`count` outputs. Rejects malformed input. | same | `ModuleNotFoundError: No module named 'stale_issue'` | 14/14 pass |
| Workflows | `build.yml` (reusable), `release.yml` (from the infra template), `refresh.yml` (cron → gate → pipeline → build → prod via `deploy-app.yml`). | same, plus actionlint | Collection error (imports `should_run`); the workflow files didn't exist | 12/12 structural tests; actionlint 1.7.12 with shellcheck 0.11.0: `Found 0 errors in 3 files` |
| `Dockerfile` / `.dockerignore` | Node 22 build of `site/` with `DATA_DIR=/src/data`; nginx-unprivileged on 8080 with Astro's 404 page. | `pytest scripts/tests -m docker` | 5 setup errors (`FileNotFoundError: Dockerfile`), and `test_dockerignore_lists_heavy_paths` FAILED | 6/6 docker tests and 1 dockerignore test pass |
| Real-site image check | Built the Dockerfile against the real `site/` with `site/tests/fixtures/data` as `data/`. | one-off `docker build` + `curl` | n/a | `/healthz` wget OK, `/` 200 (11.5 KB), `/events/ufc-323-alpha-vs-bravo/` 200, `/nope` 404 |
| Infra `sites.yml` | Added the `ufc` entry (image, 8080, `/healthz`, both hosts, default memory caps). | scratch check (PyYAML: entry present, fields, unique hosts); `terraform fmt -check`, `terraform init -backend=false && validate`; `ansible-lint site.yml` | `AssertionError: no ufc app` | `OK`; fmt clean; `The configuration is valid.`; `0 failure(s), 0 warning(s)` |

Refactor (e1cf5f7): extracted the duplicated `$GITHUB_OUTPUT` writer into `scripts/gh_output.py`, and added a test
that keeps the issue title in `refresh.yml` in sync with `stale_issue.TITLE`. All 46 tests pass after it.

## Test specification

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| 1 | Manual and daily-cron runs always do work | `test_should_run.py::test_manual_dispatch_always_runs`, `::test_daily_schedule_always_runs` | unit | PASS |
| 2 | Hourly runs work on event day D and D+1, but not D−1 or D+2 | `test_should_run.py::test_hourly_*` | unit | PASS |
| 3 | A missing index triggers a bootstrap run; a missing event file is ignored; a corrupt index fails loudly | `test_should_run.py::test_missing_*`, `::test_corrupt_index_raises` | unit | PASS |
| 4 | The CLI writes `run=true\|false` to `$GITHUB_OUTPUT` and defaults to today in UTC | `test_should_run.py::test_main_*` | unit | PASS |
| 5 | The issue body lists deduped fighters sorted by name, with ufcstats links, event names (falling back to the id), the model date and re-export steps; pipes are escaped | `test_stale_issue.py::test_body_*` | unit | PASS |
| 6 | With nothing stale, no body file is written and `stale=false count=0` is output; malformed stale files raise | `test_stale_issue.py::test_main_*`, `::test_load_stale_*` | unit | PASS |
| 7 | The daily cron in refresh.yml equals `DAILY_CRON`; the hourly cron avoids minute 0 | `test_workflows.py::test_refresh_*cron*` | static | PASS |
| 8 | Refresh runs scrape → images → score in order, caches `.cache/ufcstats`, calls the gate and stale scripts (title in sync), and deploys prod through `deploy-app.yml` without relying on tags | `test_workflows.py::test_refresh_*` | static | PASS |
| 9 | Release targets the `ufc` app and its staging/prod URLs, and triggers on `v*` tags; both flows build through `build.yml` with the fixed image name | `test_workflows.py::test_release_*`, `::test_both_flows_*` | static | PASS |
| 10 | Every third-party action is pinned to a 40-hex SHA | `test_workflows.py::test_third_party_actions_are_pinned_to_commit_shas` | static | PASS |
| 11 | actionlint, including shellcheck of `run:` blocks, is clean | `test_workflows.py::test_actionlint_passes` | lint | PASS (skips when actionlint is unavailable) |
| 12 | The build sees `data/` through `DATA_DIR`; `/healthz` answers infra's wget probe inside the container; directory URLs serve `index.html`; unknown paths serve the site's 404 page with status 404 | `test_dockerfile.py` (`-m docker`) | integration | PASS |
| 13 | The image exposes 8080/tcp and runs as non-root; host `node_modules` stay out of the image | `test_dockerfile.py::test_exposes_8080_and_runs_unprivileged`, `::test_dockerignore_keeps_host_artifacts_out` | integration | PASS |

## Coverage

```
$ ACTIONLINT=<uvx actionlint-py 1.7.12> mise x shellcheck@0.11.0 -- \
    uv run --project pipeline pytest scripts/tests -m "" --cov=scripts
scripts/gh_output.py      8   0  100%
scripts/should_run.py    43   2   95%   (utc_today body, __main__ guard)
scripts/stale_issue.py   59   1   98%   (__main__ guard)
TOTAL (incl. tests)     435   6   99%
46 passed in 2.31s
```

The default run (`pytest scripts/tests`, without docker) gives 39 passed and 1 skipped. The actionlint test is
skipped there only because this machine's mise `actionlint`/`shellcheck` shims have no version set.

## Known gaps

- The workflows are checked structurally and with actionlint, but they have never run on GitHub: there is no remote
  yet. The first real run is step 5 of the `docs/DEPLOY.md` checklist.
- `--cookie-file`: `refresh.yml` passes `--cookie-file .cache/ufcstats/cookies.json` to `fightcard scrape`. The
  scraper's `stage.py` didn't exist when this was written, though its `Client(cookie_file=...)` does. The scraper
  stage must expose that flag, or the step fails with an argparse error.
- Infra validation stopped at what runs offline: fmt, validate and ansible-lint. `terraform plan` and
  `ansible --check` need real credentials, so they run on the infra PR.
