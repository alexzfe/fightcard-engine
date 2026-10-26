# TDD evidence: fightcard-engine repo layout and CI (Phase 1 move)

**Source plan:** the Phase 1 "move" brief (orchestrator scratchpad, not in the repo). Section 4 "Move map" of the
Phase 0 recon is the spec. There is no `*.plan.md`; the journeys below come from the brief.
**Branch:** `main` of a local clone of fight-card-website, rewritten with `git filter-repo`. Nothing is pushed.

## User journeys

1. As a maintainer, I want `uv run pytest` at the repo root to run every suite (pipeline and ops), so this repo
   needs no `--project pipeline` and no path arguments.
2. As a maintainer, I want the move to change no behaviour: the same tests pass with the same counts as in the site.
3. As a maintainer, I want CI on every PR and every push to main that installs from the lockfile, runs the full
   suite and lints the workflows, with SHA-pinned actions and no default token permissions.
4. As an operator, I want `fightcard --help` to offer the stages `scrape segments images score`.

## Move

`pipeline/` became the root (`src/fightcard/`, `tests/`, `pyproject.toml`, `uv.lock`). `scripts/{should_run,
stale_issue,gh_output}.py` stay under `scripts/`. Their tests and helpers moved from `scripts/tests/` to `tests/ops/`.
`docs/ELO.md`, `docs/CONTRACT.md`, `model/` and the pipeline-related `docs/tdd/*.md` keep their paths. History is
kept for each of them through `git filter-repo --path … --path-rename pipeline/: --path-rename scripts/tests/:tests/ops/`.

## Task report

| Task | Summary | Validation run | RED (`ea58936`) | GREEN (`f943772`) |
|---|---|---|---|---|
| Layout guard `tests/test_layout.py` | From the repo root: every suite collects, ops tests import `scripts/`, score tests find `model/`, `fightcard` imports from `src/`, and `fightcard --help` lists the stages. | `uv run pytest tests/test_layout.py tests/ops/test_workflows.py -q` | 3 failed: `ModuleNotFoundError: No module named 'should_run'` / `'stale_issue'` (ops helpers looked for `tests/` instead of `scripts/`); `FileNotFoundError: model dir /home/alex/Projects/model is missing model.txt, …` (score tests used `parents[3]`). Import and `--help` already passed. | 5/5 pass |
| CI `tests/ops/test_workflows.py` + `.github/workflows/ci.yml` | Runs on PRs and pushes to main; top-level `permissions: {}`; the job only gets `contents: read`; `uv sync --frozen` comes before `uv run pytest` (no paths); actionlint runs as `uvx --from actionlint-py==1.7.12.25 actionlint`; every `uses:` is pinned to a 40-hex SHA (the pins are copied from the site's `release.yml`); and actionlint passes. | same, with `ACTIONLINT` set to a uvx wrapper | 7 failed: `FileNotFoundError: …/.github/workflows/ci.yml`; `test_actionlint_passes` saw no files | 7/7 pass; `uvx --from actionlint-py==1.7.12.25 actionlint` gives exit 0 |

GREEN changes, all paths: `tests/ops/ops_helpers.py` (`REPO = parents[2]`, `SCRIPTS = REPO / "scripts"`);
`tests/score/{conftest,test_score_parity,test_score_summary,test_score_stage}.py` (`parents[3]` → `parents[2]`);
`tests/ops/conftest.py` drops the site's docker-marker hooks (there are no docker tests here); `.gitignore`.

## Behaviour unchanged

| Suite | Site (`03a0f7e`, scratch clone) | Here, after GREEN |
|---|---|---|
| pipeline | `uv run --project pipeline pytest pipeline/tests -q`: **318 passed, 1 deselected** | `uv run pytest tests --ignore=tests/test_layout.py --ignore=tests/ops -q`: **318 passed, 1 deselected** |
| should_run + stale_issue | `uv run --project pipeline pytest scripts/tests/test_should_run.py scripts/tests/test_stale_issue.py -q`: **27 passed** | `uv run pytest tests/ops/test_should_run.py tests/ops/test_stale_issue.py -q`: **27 passed** |
| everything | n/a | `uv run pytest -q`: **357 passed, 1 deselected** with `ACTIONLINT` set; **356 passed, 1 skipped** without it (318 + 27 + 12 new) |

No test bodies changed, only path constants and one docstring (`test_images_live.py`).

## Test specification

| # | Guarantee | Test | Type | Result |
|---|---|---|---|---|
| 1 | Every suite (images, ops, score, segments, ufcstats) collects from the repo root | `test_layout.py::test_every_suite_collects_from_the_repo_root` | integration | PASS |
| 2 | The ops tests import `should_run` / `stale_issue` from `scripts/` | `test_layout.py::test_ops_scripts_run_from_the_repo_root` | integration | PASS |
| 3 | Score tests read the vendored `model/` at the repo root | `test_layout.py::test_score_tests_find_the_vendored_model_at_the_repo_root` | integration | PASS |
| 4 | The package is `fightcard` from `src/fightcard` | `test_layout.py::test_package_imports_as_fightcard_from_src` | unit | PASS |
| 5 | `fightcard --help` lists scrape, segments, images, score | `test_layout.py::test_cli_help_lists_the_stages` | integration | PASS |
| 6 | CI triggers: pull_request, push to main | `test_workflows.py::test_ci_runs_on_pull_requests_and_pushes_to_main` | structural | PASS |
| 7 | `permissions: {}` at the top; jobs ask only for `contents: read` | `test_ci_grants_no_permissions_by_default`, `test_ci_jobs_ask_only_for_read_access` | structural | PASS |
| 8 | The test job installs `--frozen`, then runs plain `uv run pytest` | `test_ci_test_job_runs_the_full_suite_from_the_locked_env` | structural | PASS |
| 9 | actionlint runs in CI, pinned to actionlint-py 1.7.12.25 | `test_ci_runs_actionlint` | structural | PASS |
| 10 | Every action is pinned to a commit SHA | `test_third_party_actions_are_pinned_to_commit_shas` | structural | PASS |
| 11 | actionlint passes on the workflows | `test_actionlint_passes` (skips without actionlint) | lint | PASS |

## Coverage and known gaps

- `uv run pytest -q --cov=fightcard`: **TOTAL 1578 stmts, 21 missed, 99%**. This is the same code as in the site.
- Running actionlint locally needed `MISE_SHELLCHECK_VERSION=0.11.0`. The mise `shellcheck` shim has no global
  version, so actionlint's shellcheck call failed with "No version is set for shim". CI's runner has its own
  shellcheck.
- `ci.yml` hasn't run on GitHub yet; the repo isn't pushed.
- The `docs/tdd/*.md` files carried over from the site still quote `pipeline/…` commands. They record what was run
  at the time, so they are left as written.
