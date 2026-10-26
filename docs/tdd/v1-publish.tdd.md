# TDD evidence: the /v1/ publisher and its nginx config (Phase 3)

**Source plan:** the Phase 3 "v1 writer" brief (orchestrator scratchpad, not in the repo). There is no `*.plan.md`;
the journeys below come from the brief. **Branch:** `feat/v1-writer`. Nothing is pushed.

## User journeys

1. As an operator, I want `fightcard publish --data data --out <root>` to refuse invalid data (exit 1), so a bad
   scrape never replaces the live API.
2. As a consumer, I want `/v1/index.json`, events, fighters, headshots, a JSON Schema and one consistent
   `snapshot.tar.gz`, formatted per the contract, with a byte-identical snapshot when nothing changed.
3. As a consumer, I never want to read a half-written or mixed tree while a publish runs.
4. As an operator, I want nginx to serve that tree on :8080 with the agreed Content-Type, Cache-Control, ETag/304,
   404 and 405 behaviour, proven against the real image.
5. As a learner or consumer, I want CONTRACT.md to be the /v1/ contract and API.md to explain it.

## Task report

| Task | Summary | Validation run | RED | GREEN |
|---|---|---|---|---|
| 1. `publish` stage (`tests/publish/test_publish_stage.py`) | `fightcard.publish.build` validates `data/` (pydantic models, event file name = id, fighter key = id, every index event exists, `generated_at` UTC `Z`) and lays out `index.json` + `contract_version: "1"`, events, fighters, headshots (not `misses.json`), `schema.json`, and a deterministic snapshot. `stage.py` registers `publish`; `--data` works before or after the stage name. | `uv run pytest tests/publish -q` | `7463e6a`: collection error `ModuleNotFoundError: No module named 'fightcard.publish'` | `768a110`: 22/22; full suite 378 passed. Also published the site's real `data/`: `publish: 140 files` |
| 2. Atomic publish (`tests/publish/test_generations.py`) | `fightcard.publish.generations.publish` writes `generations/.tmp-<id>/`, renames it, then `os.replace`s a new relative `current` symlink. Identical content is a no-op; unchanged files are hard links to the live ones; keep the last `--keep` (5); a crashed run's leftovers are cleared; 0755/0644 under any umask; `OSError` → exit 1 with `current` untouched. | same | `83dc71f`: `No module named 'fightcard.publish.generations'` | `03541a4`: 36/36, 392 total. Mutation check: with the step-1 in-place writer patched back in, the concurrent-reader test fails with `FileNotFoundError(2, 'No such file or directory')` |
| 3. nginx config (`tests/deploy/test_nginx.py`) | `deploy/nginx/fightcard-engine.conf` in `nginxinc/nginx-unprivileged:1.28-alpine@sha256:7377697a…` (`-m docker`, opt-in). Static shape checks run by default. | `uv run pytest -m docker -v -s` | `b2f7f5e`: default run 2 failed (missing config file); `-m docker` 44 errors `bind source path does not exist` | `d7161a4`: 44/44 (4 runs, no leftover containers); default 395 passed |
| 3a. ETag collision (found by the docker test) | nginx's ETag is `"<mtime s>-<size>"`. A same-size `index.json` rewritten in the same second kept ETag `"6adf9f61-93"`, so a client would get 304 for new content. A rewritten file now gets an mtime later than the one it replaces. | `uv run pytest tests/publish/test_generations.py -k later_mtime` | `fc55cca`: `assert 1793040246 > 1793040346` | in `d7161a4` |
| 3b. Dangling `current` | When the live generation was deleted by hand, publish reported "unchanged" and kept a broken link. It now writes a fresh generation. | `-k dangling` | `d761f32`: `Generation(..., changed=False).changed` | `bf0a2cf`: 433 total, docker 44/44 |
| 4–5. Docs (`tests/test_docs.py`) | CONTRACT.md is the /v1/ contract; the site-era ownership table is gone; model/README.md drops the scrapped model-export flow and the stale "not in the export yet"; docs/API.md is new, and README links it. | `uv run pytest tests/test_docs.py -q` | `5130853`: 33 failed, 4 passed (the formatting rule was already in CONTRACT) | `6b3b0fd`: 37/37, 432 total |

`pyproject.toml` adds the `docker` marker, and the default `addopts` becomes `-m 'not live and not docker'`; a
`-m docker` on the command line overrides it.

## Docker test: headers seen (`d7161a4`)

```
/v1/index.json 200      application/json; charset=utf-8  public, max-age=60     "6adf9fa0-93"
/v1/events/e1.json 200  application/json; charset=utf-8  public, max-age=300    "6adf9fa0-176"
/v1/fighters.json 200   application/json; charset=utf-8  public, max-age=300    "6adf9fa0-3a8"
/v1/schema.json 200     application/json; charset=utf-8  public, max-age=300    "6adf9fa0-3e25"
/v1/images/f1.webp 200  image/webp                       public, max-age=86400  "6adf9fa0-20"
/v1/images/f2.png 200   image/png                        public, max-age=86400  "6adf9fa0-18"
/v1/snapshot.tar.gz 200 application/gzip                 public, max-age=60     "6adf9fa0-9c8"
If-None-Match → 304 with the same ETag and Cache-Control (index, webp, snapshot)
etags before {index "6adf9fa0-93", f1.webp "6adf9fa0-20", snapshot "6adf9fa0-9c8"}
      after  {index "6adf9fa1-93", f1.webp "6adf9fa0-20", snapshot "6adf9fa1-9c7"}   (new generation; headshot unchanged)
```

## Test specification

| # | Guarantee | Test | Type | Result |
|---|---|---|---|---|
| 1 | Every /v1 file is published, `misses.json` isn't | `test_publish_writes_every_v1_file`, `test_headshots_are_copied_and_misses_json_is_not` | integration | PASS |
| 2 | index = data index + `contract_version "1"`, `generated_at` kept | `test_index_adds_contract_version_and_keeps_generated_at` | integration | PASS |
| 3 | JSON follows the formatting rule | `test_json_follows_the_formatting_rule` | integration | PASS |
| 4 | Schema has `$defs` Index (with `contract_version`), Event, Fighter, Fighters | `test_schema_describes_the_top_level_types` | unit | PASS |
| 5 | Snapshot = every other file; byte-identical for identical input; mtime/uid/gid 0, gzip mtime 0, sorted | `test_snapshot_holds_every_other_file`, `test_snapshot_is_deterministic` | unit | PASS |
| 6 | Invalid data → exit 1, nothing published (8 cases) | `test_refuses_*` | integration | PASS |
| 7 | `current` is a relative symlink to a complete generation; a new one switches it | `test_current_is_a_relative_symlink_…`, `test_a_new_generation_switches_…` | integration | PASS |
| 8 | Identical content: no new generation, same inode and mtime (same ETag) | `test_identical_content_publishes_nothing` | integration | PASS |
| 9 | Unchanged files shared, changed files get a later mtime | `test_unchanged_files_are_shared_…`, `test_a_changed_file_gets_a_later_mtime_…` | integration | PASS |
| 10 | Keep last N; ids sort in publish order; leftovers cleared; dangling `current` repaired | `test_keeps_the_last_n_generations`, `test_generation_ids_sort_…`, `test_a_crashed_runs_leftovers_are_cleared`, `test_a_dangling_current_is_republished` | integration | PASS |
| 11 | 0755/0644 under umask 077 | `test_published_tree_is_world_readable_whatever_the_umask` | integration | PASS |
| 12 | A write failure (ENOSPC) or invalid data leaves `current` live; exit 1 | `test_a_failure_mid_write_…`, `test_invalid_data_keeps_…`, `test_a_write_failure_exits_1` | integration | PASS |
| 13 | 3 readers during 39 publishes: `current/index.json` always readable, every resolved generation self-consistent | `test_readers_never_see_a_mixed_or_missing_generation` (15 repeat runs, 0 flakes) | concurrency | PASS |
| 14 | nginx: 200 + type + cache per resource, healthz, 304, HEAD, 404 ×14 paths, 405 ×15 method/path pairs with `Allow`, no version in `Server`, snapshot = served files, ETags stable and following content | `tests/deploy/test_nginx.py` (`-m docker`) | container | PASS 44/44 |
| 15 | Docs: endpoints and cache policies in CONTRACT, API.md terms, README link, model-export drift gone | `tests/test_docs.py` | structural | PASS 37/37 |

## Coverage and known gaps

- `uv run pytest -q --cov=fightcard.publish`: **build.py 97%, generations.py 100%, stage.py 97%, total 98%**
  (missed: the "not an object" branches for index/fighters, and the `_positive` error message).
  `uv run pytest -q`: **434 passed, 1 skipped, 45 deselected** (433 before the fighter-reference check).
- CI (`ci.yml`) runs the default suite only, so the docker tests run locally. The static config checks run in CI.
- No lock against two concurrent publishers on one root (documented in `generations.py` and CONTRACT).
- Out of scope, per the brief: the service Dockerfile, the scheduler, infra `sites.yml`, the site's refresh.
