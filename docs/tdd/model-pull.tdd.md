# TDD evidence: the engine pulls the model (Phase 5)

Source: the orchestrator's Phase 5 brief and the shared contract (`phase5-contract.md`: public R2 bucket
`fightcard-models`, `latest.json` written last, `<version>/<file>` immutable). The research side (publisher and
export gate) is built separately against the same contract. Branch `feat/model-pull`, from `origin/main` at 85a8f8e.
Baseline: 696 passed, 1 skipped. Final: 885 passed, 1 skipped (821 before the review fixes below).

## User journeys

1. As the site owner, I want a new finish-model export to reach the box without a release or a copy into `model/`.
2. As the site owner, I want a bad export (corrupt, not scoring as research exported it, other tier cut points) to
   change nothing, and the reason in the logs.
3. As the site owner, I want a flaky or down bucket to never fail a refresh.
4. As the site owner, I want to pin the box to a kept version, which is also how I roll back.
5. As research's CI, I want `/v1/index.json` to say which model version the box runs and how many fighters are
   stale, so I only re-export when the newest model has gone stale.

## Task report

| Deliverable | RED | GREEN | Command | RED evidence | GREEN evidence |
|---|---|---|---|---|---|
| `score.parity.parity_failures` (the examples.json check, now a function) | bdd0644 | 0e0982c | `uv run pytest tests/score/test_score_parity.py` | 6 failed, 2 passed: no `fightcard.score.parity` | 8 passed |
| `index.model.stale_fighters` (score writes it; publish and schema.json carry it) | 57f16f0 | c8aa267 | `uv run pytest tests/score tests/publish` | 2 failed: `ModelInfo` has no `stale_fighters`; `KeyError: 'stale_fighters'` in the published index | 104 passed |
| `fightcard model pull`: no-op without a url, the command, first-boot seed | fd6b74b | 36fa9fc | `uv run pytest tests/model tests/test_layout.py` | 9 failed: `invalid choice: 'model'` | 14 passed |
| Adopt a new version, unchanged is a no-op, prune to 3 | 256044b | 7c464ea | `uv run pytest tests/model` | 9 failed, 9 passed | 18 passed |
| Holds: checksum, load, manifest, parity, moved cut points, bad `latest.json`, unreachable bucket | 0da948d | 33faeb5 | `uv run pytest tests/model` | 32 failed, 30 passed | 62 passed |
| Pin and rollback | 7de2cef | 0809fcd | `uv run pytest tests/model` | 12 failed, 63 passed | 75 passed |
| Runner: non-fatal `model` step before score, score reads `state/model/current`; `-m docker` pull from a local server | 116c7fe | 69b6575 | `uv run pytest tests/service tests/test_docs.py`; `uv run pytest tests/deploy/test_image.py -m docker` | 15 failed, 94 passed in `tests/service`; `-m docker`: `test_a_run_pulls_a_model_…` failed on the missing `step=model` line (run with `-x`, so the unreachable-bucket test wasn't reached) | 182 passed; `-m docker` image tests 12/12 |
| Docs: SERVICE.md, CONTRACT.md, API.md, model/README.md, README | 9ad4a37 | 6eb2fbe | `uv run pytest tests/test_docs.py` | 28 failed, 73 passed | 106 passed (docs + layout) |
| `current` is not a version name | 017ca67 | 066f6ec | `uv run pytest tests/model` | 1 failed, 8 passed (found on review) | 76 passed |
| Review 2: `parity_failures` holds on any exception (malformed `p_finish`, `LightGBMError`) | c916397 | 2d19b54 | `uv run pytest tests/score/test_score_parity.py` | 4 failed, 21 passed (string, `None`, dict `p_finish` raised `TypeError`; a raising booster propagated) | 25 passed |
| Review 9: sweep stray `.current-*` symlinks | a725f6c | 10ad28c | `uv run pytest tests/model` | 1 failed | 77 passed |
| Review 7: an unknown or unusable pin logs `ERROR` every run | 4c39f57 | bda8f71 | `uv run pytest tests/model -k pin` | 2 failed, 13 passed | 15 passed |
| Review 10: redirects only to http(s) | 8a1d584 | 27e2653 | `uv run pytest tests/model` | 3 failed, 1 passed (urllib follows an http to ftp redirect; `file:` and `gopher:` it already refused, with its own message: those two cases are regression guards) | 83 passed |
| Review 5: no silent downgrade (`as_of` older than the running model's is held; a missing or malformed `as_of` is held) | 37fd45f | 1956ce9 | `uv run pytest tests/model` | 5 failed, 83 passed (the old "`latest.json` pointing back rolls back" test became "is held") | 88 passed |
| Review 1: `.held-<version>` marker, written before the gate; no refetch while version + digests + running version match | d70a82d | 66993e4 | `uv run pytest tests/model` | 5 failed, 92 passed | 98 passed (the GREEN also corrects two RED tests, see the notes) |
| Review 3: a baked model with a newer `as_of` replaces an older `current` (gate minus the cut points) | ff3bb8f | 1763bf9 | `uv run pytest tests/model` | 6 failed, 5 passed | 105 passed |
| Review 4: score reads `state/model/current` only when a pull is configured; `--fallback-model-dir`; score logs which model | 7f1d295 | 4ce968d | `uv run pytest tests/service tests/score` | 7 failed, 88 passed | 196 passed |
| Review 11: `contract_version` `"1.1"` (index.json, schema.json title, CONTRACT.md Versioning) | 2f1a418 | 6b28b06 | `uv run pytest tests/publish tests/test_docs.py` | 3 failed, 139 passed | 142 passed |
| Review: docs for all of the above (SERVICE.md "The model") | 3c73c6e | 6405a4d | `uv run pytest tests/test_docs.py` | 9 failed, 104 passed | 113 passed |

Final runs (after the review fixes): `uv run pytest` 885 passed, 1 skipped; `uv run pytest -m docker` 56 passed (the
image tests pull from a local bucket); `-m seed` 6 passed; actionlint as `release.yml` runs it,
`mise x shellcheck@0.11.0 -- uvx --from actionlint-py==1.7.12.25 actionlint`, exit 0 (the bare command fails here
only because this machine's mise shellcheck shim has no version set, as in `ops.tdd.md`).

Notes on the RED commits:

- The holds RED first segfaulted the interpreter: with no gates, the pull adopted a byte-flipped `model.txt`, and
  the next pull loaded it with `lgb.Booster`. Hence the gate order (sha256 before anything is loaded) and tests that
  seed first and use a bad-but-loadable version where a later pull follows (`seeded` fixture).
- The runner RED left three test setups wrong, fixed in its GREEN: a `current` symlink to a directory that didn't
  exist (two tests), and the fetch-off stage list (now `model, score, publish`). The `kept()` helper in the
  `36fa9fc` GREEN skips the `current` symlink.
- Some tests pass at RED because they pin behaviour an earlier GREEN already gave (an unreachable bucket, the size
  cap, http(s)-only urls, equal cut points with other evidence): they are regression guards, not new behaviour.

Notes on the review fixes (rows "Review …"; each is a RED commit, then a GREEN commit):

- The review's findings 6 and 8 were not in the brief for this round. Each finding that was was verified against the
  code first and all were real (the isclose loop sat outside the `try`; urllib's redirect handler allows `ftp:`; a
  newer baked model was ignored once state existed; score preferred `state/model/current` with no pull configured).
- Review 1, a decision: the marker is written for a **gate verdict** and for a **pull that dies inside the gate**
  (a segfault leaves "the pull ended inside the gate"), not for a checksum mismatch. A verdict is a function of
  the version, its digests and the running model, so skipping it while those are unchanged is safe. A mismatch
  says nothing about the model (the transfer, or the bucket, may be what broke, and re-uploading the same bytes
  keeps the same digests), so it is fetched again each run. The first RED used `corrupt=` for the marker tests;
  the GREEN switched those two to an `examples.json` verdict and added `test_a_checksum_mismatch_is_no_verdict…`.
  An exception raised inside the gate (not only `Held`) is now a hold with a marker.
- Review 5: both `latest.json`'s `as_of` (before any fetch) and the downloaded `manifest.json`'s `as_of` (in the
  gate) are compared with the running model's; equal dates are adopted (a same-day re-export).
- Review 3: under a pin the newer baked model is kept (so it can be pinned) but not switched to; a baked model that is
  not newer is logged every run, as asked.
- Review 4: score gets `--fallback-model-dir` rather than the runner probing the bundle (importing LightGBM into the
  supervisor process is avoided). With no fallback a model dir that won't load still fails as it did.
- Review 2/10 RED notes: some malformed examples (bad tier, bad date or context) already held at RED; they are guards.

## Test specification

| # | What is guaranteed | Test | Type | Result |
|---|---|---|---|---|
| 1 | `parity_failures` is empty for the vendored model; names the example whose `p_finish` or tier moved; rejects an empty or malformed examples file | `tests/score/test_score_parity.py::test_parity_failures_*` | unit | PASS |
| 2 | `index.model.stale_fighters` is the number of distinct stale fighters (0 when none); publish carries it; schema.json lists it as optional (additive) | `test_score_stage.py::test_index_model_counts_the_stale_fighters`, `test_publish_stage.py::test_index_model_stale_fighters_is_published_and_in_the_schema` | unit | PASS |
| 3 | No url (and no pin) is a no-op that creates nothing; the url and pin default to the environment; the root defaults to beside `--data` | `tests/model/test_pull.py::test_without_a_url_it_does_nothing`, `…_defaults_*` | unit | PASS |
| 4 | First boot seeds `state/model/<baked version>` and a relative `current` from the baked model (bundle files only), with or without a reachable bucket; repeats and a broken `current` are handled; a missing baked model is logged, exit 0 | `test_the_first_boot_*`, `test_the_seed_*`, `test_a_current_that_no_longer_loads_*`, `test_a_missing_baked_model_*` | unit | PASS |
| 5 | A differing version is fetched at `<version %2B-encoded>/<file>`, swapped in atomically, the previous one kept; unchanged downloads nothing; `latest.json` pointing back rolls back; the last 3 (`--keep`) survive, never `current`; staging leftovers are cleared | `test_a_new_version_is_adopted_*`, `test_the_plus_in_a_version_*`, `test_an_unchanged_version_*`, `test_only_the_last_three_*`, `test_nothing_is_left_behind_*` | unit (local http server) | PASS |
| 6 | Held, current kept, reason logged, exit 0: a bad checksum (4 files), a bundle that doesn't load, a manifest for another version, an example whose `p_finish` or tier moved, moved `low_max` or `medium_max`; equal cut points with other evidence are adopted; judged against the running model, not the baked one | `test_a_file_that_fails_its_checksum_*`, `test_a_bundle_that_does_not_load_*`, `test_a_manifest_for_another_version_*`, `test_an_example_whose_*`, `test_moved_tier_cut_points_*` | unit | PASS |
| 7 | A `latest.json` with another `format_version`, an unsafe, empty or reserved version, a non-object `files`, a non-bundle or path-like file name, a bad digest or a missing required file is held before any file is fetched | `test_a_latest_that_is_not_the_published_shape_*`, `test_a_file_name_outside_the_bundle_*`, `test_a_bundle_without_a_required_file_*` | unit | PASS |
| 8 | An unreachable bucket, a 404, unreadable JSON, a missing file, a file over the size cap, a non-http(s) url: `failed, keeping`, current kept, exit 0 | `test_an_unreachable_or_unreadable_bucket_*`, `test_a_file_over_the_size_cap_*`, `test_only_http_urls_are_fetched` | unit | PASS |
| 9 | Pin: switches to a kept, loadable version without touching the bucket (with or without a url, from the environment, on the first boot), survives newer versions, unpinning resumes, anything else is held, the pin is never pruned | `test_a_pin_*`, `test_unpinning_resumes_pulling`, `test_the_pin_defaults_to_the_environment`, `test_the_pinned_version_is_never_pruned` | unit | PASS |
| 10 | The runner runs `model pull` just before score (also with fetch off), passes `--root`, `--seed-dir`, `--url`, `--pin`; score reads `state/model/current` once it exists (even one made during the run), else `FIGHTCARD_MODEL_DIR`; a pull that fails, hangs or can't start never fails the run; the step has a 5 min timeout | `tests/service/test_runner.py::test_the_model_pull_*`, `test_score_*`, `test_a_*pull*`; `test_supervision.py::test_the_timeouts_*` | unit | PASS |
| 11 | In the image: a run with `FIGHTCARD_MODEL_URL` pointing at a `python -m http.server` bucket inside the container seeds, adopts, scores with the pulled version, and `/v1/index.json` says `model.version`, `model.as_of`, `model.stale_fighters`; an unreachable bucket keeps the run green and the model | `tests/deploy/test_image.py::test_a_run_pulls_a_model_from_a_local_bucket_and_scores_with_it`, `::test_an_unreachable_bucket_keeps_the_run_and_the_model` | e2e (docker) | PASS |
| 12 | The docs describe all of it (gates, hold, pin, rollback, the fields, the automated re-export) | `tests/test_docs.py` (Phase 5 block) | docs | PASS |
| 13 | `parity_failures` holds on any exception; a malformed `p_finish`, tier or bout and a raising booster are failures, not crashes | `test_score_parity.py::test_parity_failures_holds_*` | unit | PASS |
| 14 | A gate verdict or a crash in the gate leaves `.held-<version>` (digests, running version, reason); while it matches nothing is fetched and the hold is logged each run; a changed digest or running version retries; adoption or a newer `latest.json` clears it; a failed download or a checksum mismatch leaves none | `test_pull.py::test_a_held_version_is_not_downloaded_again_*`, `test_the_marker_*`, `test_a_pull_that_dies_in_the_gate_*`, `test_a_checksum_mismatch_*` | unit | PASS |
| 15 | A newer baked model replaces an older `current` (cut points exempt), is only kept under a pin, fails closed on its examples; an older one is logged; an older `latest.json` is held | `test_pull.py::test_a_newer_baked_model_*`, `test_an_older_baked_model_*`, `test_a_pin_still_wins_*` | unit | PASS |
| 16 | `latest.json` and `manifest.json` `as_of` not older than the running model's (equal is fine), valid `as_of` required; rollback only by pin | `test_pull.py::test_a_latest_that_points_back_*`, `test_a_manifest_older_*`, `test_a_version_with_the_same_as_of_*` | unit | PASS |
| 17 | An unusable pin is an `ERROR` line every run; `.current-*` are swept; a redirect to a non-http(s) scheme is refused | `test_pull.py::test_a_pin_that_cannot_be_honoured_*`, `test_a_current_symlink_*`, `test_a_redirect_*` | unit | PASS |
| 18 | The runner passes `current` (and the baked model as fallback) only when a pull is configured; score falls back with an `ERROR` and says which model it uses | `test_runner.py::test_score_*`, `test_score_stage.py::test_a_model_dir_that_does_not_load_*`, `test_score_says_which_model_*` | unit | PASS |
| 19 | `contract_version` is `"1.1"`, and the docs say what it holds | `test_publish_stage.py`, `test_docs.py::test_contract_versioning_*` | unit/docs | PASS |

## Gaps and decisions

- No test touches the network or the real bucket: every pull runs against a local `http.server` over a tmp dir, and
  the docker test serves its bucket from inside the container. The real R2 bucket, its public hostname and infra's
  `FIGHTCARD_MODEL_URL` don't exist yet, so the pull has never run against Cloudflare.
- The sha256 in `latest.json` comes from the same bucket as the files, so it catches corruption and partial uploads,
  not a bucket that someone else controls. A crafted model with a matching digest still has to load and re-score its
  examples, but `load_bundle` runs in the pull's own process: LightGBM segfaults on some corrupt model files, which
  would end that process (the run goes on, and the next run tries again).
- A held version is not fetched again while its `.held-<version>` marker matches (same digests, same running
  model); a checksum mismatch is fetched again each run (see the review notes).
- `contract_version` is now `"1.1"` (it was `"1"` although `Result.outcome`, the rating fields and the model summary
  were already additive changes); CONTRACT.md's Versioning lists what each version holds.
- `model.version` and `model.as_of` already existed in `index.model`; only `stale_fighters` is new. It counts
  distinct fighters, while `stale_fighters.json` lists one entry per fighter per event.
- A pin without a url still works (rollback shouldn't need the bucket), a small step past "url unset = no-op".
