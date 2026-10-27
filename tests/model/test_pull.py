"""`fightcard model pull`: the engine adopts the model research publishes (docs/SERVICE.md), against a fake bucket."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from model_bucket import BAKED_VERSION, BUNDLE, MODEL_DIR, Redirector, sha256, write_latest

from fightcard.cli import main
from fightcard.model import pull as pull_module
from fightcard.score.bundle import load_bundle

NEW = "2026-10-21+aaaaaaaa"


@pytest.fixture
def root(tmp_path) -> Path:
    """The model state dir, <FIGHTCARD_ROOT>/state/model."""
    return tmp_path / "state" / "model"


def pull(root: Path, *extra: str, url: str | None = None, seed: Path = MODEL_DIR) -> int:
    args = ["model", "pull", "--root", str(root), "--seed-dir", str(seed)]
    if url is not None:
        args += ["--url", url]
    return main([*args, *extra])


def current(root: Path) -> str:
    """The version `current` points at."""
    return os.readlink(root / "current")


def kept(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith("."))


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    for var in ("FIGHTCARD_MODEL_URL", "FIGHTCARD_MODEL_PIN", "FIGHTCARD_MODEL_DIR"):
        monkeypatch.delenv(var, raising=False)


# Disabled, and the command.

def test_the_cli_lists_model(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "model" in capsys.readouterr().out


def test_without_a_url_it_does_nothing(root, capsys):
    assert pull(root) == 0
    assert not root.exists()
    assert "FIGHTCARD_MODEL_URL" in capsys.readouterr().err


def test_the_url_defaults_to_the_environment(root, bucket, monkeypatch):
    monkeypatch.setenv("FIGHTCARD_MODEL_URL", bucket.url)
    assert pull(root) == 0
    assert current(root) == BAKED_VERSION  # an environment url enables the pull (the first boot seeds)


def test_the_root_defaults_to_beside_the_data_dir(tmp_path, bucket):
    assert main(["--data", str(tmp_path / "state" / "data"), "model", "pull", "--url", bucket.url,
                 "--seed-dir", str(MODEL_DIR)]) == 0
    assert (tmp_path / "state" / "model" / "current").is_symlink()


# First boot: current is seeded from the image's baked model.

def test_the_first_boot_seeds_current_from_the_baked_model(root, bucket):
    bucket.publish(BAKED_VERSION)  # the bucket is already on the baked version
    assert pull(root, url=bucket.url) == 0
    assert current(root) == BAKED_VERSION
    assert not os.path.isabs(os.readlink(root / "current"))  # relative, like publish's current
    for name in BUNDLE:
        assert sha256(root / "current" / name) == sha256(MODEL_DIR / name), name
    assert not (root / "current" / "README.md").exists()
    assert load_bundle(root / "current").manifest["version"] == BAKED_VERSION


def test_the_seed_is_made_even_when_the_bucket_is_unreachable(root, bucket, capsys):
    url = bucket.url
    bucket.stop()
    assert pull(root, url=url) == 0
    assert current(root) == BAKED_VERSION
    assert "seed" in capsys.readouterr().err


def test_a_second_run_keeps_the_seed_as_it_is(root, bucket):
    bucket.publish(BAKED_VERSION)
    pull(root, url=bucket.url)
    marker = root / BAKED_VERSION / "model.txt"
    stamp = marker.stat().st_mtime_ns
    assert pull(root, url=bucket.url) == 0
    assert marker.stat().st_mtime_ns == stamp
    assert kept(root) == [BAKED_VERSION]


def test_a_current_that_no_longer_loads_is_seeded_again(root, bucket):
    pull(root, url=bucket.url)
    (root / "current" / "model.txt").write_text("garbage")
    assert pull(root, url=bucket.url) == 0
    assert sha256(root / "current" / "model.txt") == sha256(MODEL_DIR / "model.txt")


def test_a_missing_baked_model_is_logged_and_does_not_fail(root, bucket, tmp_path, capsys):
    assert pull(root, url=bucket.url, seed=tmp_path / "nowhere") == 0
    assert not (root / "current").exists()
    assert "seed" in capsys.readouterr().err



# An image with a newer baked model: it replaces an older current (through the gate, minus the cut-point check).

NEWER_BAKED = "2026-11-04+bake0001"


@pytest.fixture
def newer_image(tmp_path) -> Path:
    """The baked model dir of a later image: a newer export whose tier cut points moved."""
    image = tmp_path / "image-model"
    shutil.copytree(MODEL_DIR, image)
    edit_json("manifest.json", version=NEWER_BAKED, as_of="2026-11-04")(image)
    thresholds = json.loads((image / "thresholds.json").read_text())
    edit_json("thresholds.json", low_max=thresholds["low_max"] + 1e-6)(image)
    return image


def test_a_newer_baked_model_replaces_an_older_current(root, bucket, newer_image, capsys):
    bucket.publish(NEW)
    pull(root, url=bucket.url)
    pull(root, url=bucket.url)
    assert current(root) == NEW
    capsys.readouterr()
    assert pull(root, url=bucket.url, seed=newer_image) == 0  # the cut points moved: the image defines them
    assert current(root) == NEWER_BAKED
    assert sha256(root / "current" / "thresholds.json") == sha256(newer_image / "thresholds.json")
    assert NEW in kept(root)
    assert "baked" in capsys.readouterr().err
    assert not list(root.glob(".tmp-*"))


def test_a_version_with_the_new_cut_points_is_pulled_after_the_image_moved_them(root, bucket, newer_image):
    pull(root, url=bucket.url)
    pull(root, url=bucket.url, seed=newer_image)
    later = "2026-11-11+cccccccc"
    bucket.publish(later, as_of="2026-11-11", edit=lambda d: shutil.copy2(newer_image / "thresholds.json", d))
    pull(root, url=bucket.url, seed=newer_image)
    assert current(root) == later


def test_an_older_latest_than_the_newer_baked_model_is_held(root, bucket, newer_image, capsys):
    pull(root, url=bucket.url)
    pull(root, url=bucket.url, seed=newer_image)
    bucket.publish(NEW)  # as_of 2026-10-21, older than the image's model
    capsys.readouterr()
    pull(root, url=bucket.url, seed=newer_image)
    err = capsys.readouterr().err
    assert current(root) == NEWER_BAKED and f"held {NEW}" in err and "older" in err


def test_a_newer_baked_model_that_fails_its_examples_is_not_adopted(root, bucket, newer_image, capsys):
    pull(root, url=bucket.url)
    edit_examples(p_finish=0.123456)(newer_image)
    capsys.readouterr()
    assert pull(root, url=bucket.url, seed=newer_image) == 0
    err = capsys.readouterr().err
    assert current(root) == BAKED_VERSION and NEWER_BAKED not in kept(root)
    assert f"held baked {NEWER_BAKED}" in err and "example 3" in err, err


def test_an_older_baked_model_is_kept_out_and_the_difference_is_logged(root, bucket, capsys):
    bucket.publish(NEW)
    pull(root, url=bucket.url)
    pull(root, url=bucket.url)
    capsys.readouterr()
    assert pull(root, url=bucket.url) == 0
    err = capsys.readouterr().err
    assert current(root) == NEW
    assert f"baked model {BAKED_VERSION}" in err and f"current {NEW}" in err, err


def test_a_baked_model_that_is_the_current_is_not_mentioned(root, bucket, capsys):
    pull(root, url=bucket.url)
    capsys.readouterr()
    pull(root, url=bucket.url)
    assert "baked" not in capsys.readouterr().err


def test_a_pin_still_wins_over_a_newer_baked_model(root, bucket, newer_image):
    bucket.publish(NEW)
    pull(root, url=bucket.url)
    pull(root, url=bucket.url)
    pull(root, "--pin", NEW, url=bucket.url, seed=newer_image)
    assert current(root) == NEW and NEWER_BAKED in kept(root)  # kept, so it can be pinned next
    pull(root, "--pin", NEWER_BAKED, url=bucket.url, seed=newer_image)
    assert current(root) == NEWER_BAKED


# A newer version: downloaded, checked, and swapped in.

def test_a_new_version_is_adopted_and_the_previous_one_kept(root, bucket, capsys):
    pull(root, url=bucket.url)
    bucket.publish(NEW)
    assert pull(root, url=bucket.url) == 0
    assert current(root) == NEW
    assert kept(root) == sorted([BAKED_VERSION, NEW])
    for name in BUNDLE:
        assert sha256(root / "current" / name) == sha256(bucket.root / NEW / name), name
    assert load_bundle(root / "current").manifest["version"] == NEW
    err = capsys.readouterr().err
    assert f"adopted {NEW}" in err and BAKED_VERSION in err


def test_the_plus_in_a_version_is_url_encoded(root, bucket):
    bucket.publish(NEW)
    pull(root, url=bucket.url)
    files = [r for r in bucket.requests if r != "/latest.json"]
    assert files and all(r.startswith("/2026-10-21%2Baaaaaaaa/") for r in files), files
    assert sorted(r.rsplit("/", 1)[1] for r in files) == sorted(BUNDLE)


def test_a_url_with_a_trailing_slash_or_a_path_works(root, bucket):
    bucket.publish(NEW)
    assert pull(root, url=bucket.url + "/") == 0
    assert current(root) == NEW


def test_an_unchanged_version_downloads_no_files(root, bucket, capsys):
    bucket.publish(BAKED_VERSION)
    pull(root, url=bucket.url)
    capsys.readouterr()
    bucket.requests.clear()
    assert pull(root, url=bucket.url) == 0
    assert bucket.requests == ["/latest.json"]
    assert current(root) == BAKED_VERSION
    assert "up to date" in capsys.readouterr().err


def test_a_repeat_after_adopting_downloads_nothing_either(root, bucket):
    bucket.publish(NEW)
    pull(root, url=bucket.url)
    bucket.requests.clear()
    pull(root, url=bucket.url)
    assert bucket.requests == ["/latest.json"]


def test_a_latest_that_points_back_at_an_older_version_is_held_not_a_silent_downgrade(root, bucket, capsys):
    bucket.publish(NEW, as_of="2026-10-28")
    pull(root, url=bucket.url)
    assert current(root) == NEW
    bucket.publish(BAKED_VERSION, as_of="2026-10-14")  # research re-pointed latest.json at the previous version
    capsys.readouterr()
    bucket.requests.clear()
    assert pull(root, url=bucket.url) == 0
    err = capsys.readouterr().err
    assert current(root) == NEW and bucket.requests == ["/latest.json"]  # held before any file is fetched
    assert f"held {BAKED_VERSION}" in err and "older" in err and "FIGHTCARD_MODEL_PIN" in err, err


def test_a_version_with_the_same_as_of_is_adopted(root, bucket):
    bucket.publish("2026-10-21+aaaaaaaa", as_of="2026-10-21")
    pull(root, url=bucket.url)
    bucket.publish("2026-10-21+bbbbbbbb", as_of="2026-10-21")  # a re-export the same day
    pull(root, url=bucket.url)
    assert current(root) == "2026-10-21+bbbbbbbb"


@pytest.mark.usefixtures("seeded")
def test_a_manifest_older_than_the_running_model_is_held_whatever_latest_says(root, bucket, capsys):
    bucket.publish(NEW, as_of="2026-10-21", edit=edit_json("manifest.json", as_of="2026-10-01"))
    held(root, bucket, capsys, "older")


def test_only_the_last_three_versions_are_kept(root, bucket):
    versions = ["2026-10-21+aaaaaaaa", "2026-10-28+bbbbbbbb", "2026-11-04+cccccccc", "2026-11-11+dddddddd"]
    for v in versions:
        bucket.publish(v, as_of=v[:10])
        pull(root, url=bucket.url)
    assert current(root) == versions[-1]
    assert kept(root) == versions[1:]  # the baked 2026-10-14 and the first pulled one are pruned


def test_keep_is_configurable_and_the_current_is_never_pruned(root, bucket):
    bucket.publish("2026-10-21+aaaaaaaa", as_of="2026-10-21")
    pull(root, "--keep", "1", url=bucket.url)
    assert kept(root) == ["2026-10-21+aaaaaaaa"]
    assert current(root) == "2026-10-21+aaaaaaaa"


def test_nothing_is_left_behind_in_the_state_dir(root, bucket):
    (root / ".tmp-leftover").mkdir(parents=True)  # a crashed pull's staging dir
    (root / ".tmp-leftover" / "model.txt").write_text("half")
    bucket.publish(NEW)
    pull(root, url=bucket.url)
    assert sorted(p.name for p in root.iterdir()) == sorted(["current", BAKED_VERSION, NEW])


def test_a_current_symlink_a_crashed_switch_left_behind_is_swept(root, bucket):
    pull(root, url=bucket.url)
    (root / ".current-4242").symlink_to(BAKED_VERSION)  # a pull killed between symlink() and os.replace()
    (root / ".current-dangling").symlink_to("nowhere")
    bucket.publish(NEW)
    pull(root, url=bucket.url)
    assert sorted(p.name for p in root.iterdir()) == sorted(["current", BAKED_VERSION, NEW])


# A version that fails a gate is held: current stays, the reason is logged, the run goes on (exit 0).

@pytest.fixture
def seeded(root, bucket):
    """The first boot, done against an empty bucket: the version a test publishes is the first the pull sees."""
    pull(root, url=bucket.url)


def edit_json(name: str, **changes):
    def edit(directory: Path) -> None:
        doc = json.loads((directory / name).read_text())
        doc.update(changes)
        (directory / name).write_text(json.dumps(doc))
    return edit


def edit_examples(**changes):
    def edit(directory: Path) -> None:
        examples = json.loads((directory / "examples.json").read_text())
        examples[3].update(changes(examples[3]) if callable(changes) else changes)
        (directory / "examples.json").write_text(json.dumps(examples))
    return edit


def assert_kept_out(root: Path, version: str = NEW, keeping: str = BAKED_VERSION) -> None:
    assert current(root) == keeping
    assert version not in kept(root)
    left = sorted(p.name for p in root.iterdir() if not p.name.startswith(".held-"))
    assert left == sorted(["current", *kept(root)])  # no staging left behind (a .held-<version> marker may be)


def held(root: Path, bucket, capsys, reason: str, version: str = NEW, keeping: str = BAKED_VERSION) -> None:
    capsys.readouterr()
    assert pull(root, url=bucket.url) == 0
    err = capsys.readouterr().err
    assert f"held {version}" in err and reason in err, err
    assert_kept_out(root, version, keeping)


@pytest.mark.parametrize("name", ["model.txt", "examples.json", "fighter_snapshot.parquet", "thresholds.json"])
@pytest.mark.usefixtures("seeded")
def test_a_file_that_fails_its_checksum_holds_the_version(root, bucket, capsys, name):
    bucket.publish(NEW, corrupt=name)
    held(root, bucket, capsys, f"{name}: sha256")


@pytest.mark.usefixtures("seeded")
def test_a_checksum_in_latest_that_is_not_a_digest_is_held(root, bucket, capsys):
    doc = bucket.publish(NEW, latest=False)
    write_latest(bucket.root, {**doc, "files": {**doc["files"], "model.txt": "abc"}})
    held(root, bucket, capsys, "model.txt")


@pytest.mark.usefixtures("seeded")
def test_a_bundle_that_does_not_load_is_held(root, bucket, capsys):
    bucket.publish(NEW, edit=lambda d: (d / "model.txt").write_text("not a booster"))  # hashed after: checksum ok
    held(root, bucket, capsys, "load")


@pytest.mark.usefixtures("seeded")
def test_a_manifest_for_another_version_is_held(root, bucket, capsys):
    bucket.publish(NEW, edit=edit_json("manifest.json", version="2026-10-22+bbbbbbbb"))
    held(root, bucket, capsys, "manifest.json")


@pytest.mark.usefixtures("seeded")
def test_an_example_whose_p_finish_moved_holds_the_version(root, bucket, capsys):
    bucket.publish(NEW, edit=edit_examples(p_finish=0.123456))
    held(root, bucket, capsys, "example 3")


@pytest.mark.usefixtures("seeded")
def test_an_example_whose_tier_moved_holds_the_version(root, bucket, capsys):
    bucket.publish(NEW, edit=edit_examples(tier="nonsense"))
    held(root, bucket, capsys, "example 3")


@pytest.mark.parametrize("cut", ["low_max", "medium_max"])
@pytest.mark.usefixtures("seeded")
def test_moved_tier_cut_points_hold_the_version(root, bucket, capsys, cut):
    old = json.loads((MODEL_DIR / "thresholds.json").read_text())[cut]
    bucket.publish(NEW, edit=edit_json("thresholds.json", **{cut: old + 1e-6}))
    held(root, bucket, capsys, cut)


@pytest.mark.usefixtures("seeded")
def test_equal_cut_points_with_other_evidence_are_adopted(root, bucket):
    bucket.publish(NEW, edit=edit_json("thresholds.json", oos={"n": 1, "log_loss": 0.6, "constant_log_loss": 0.69}))
    pull(root, url=bucket.url)
    pull(root, url=bucket.url)
    assert current(root) == NEW


@pytest.mark.usefixtures("seeded")
def test_a_held_version_is_judged_against_the_model_that_is_running_not_the_baked_one(root, bucket, capsys):
    bucket.publish(NEW)
    pull(root, url=bucket.url)
    assert current(root) == NEW
    bucket.publish("2026-10-28+bbbbbbbb", as_of="2026-10-28", edit=edit_examples(p_finish=0.123456))
    capsys.readouterr()
    assert pull(root, url=bucket.url) == 0
    assert current(root) == NEW and "held 2026-10-28+bbbbbbbb" in capsys.readouterr().err


@pytest.mark.usefixtures("seeded")
def test_a_good_version_after_a_held_one_is_adopted(root, bucket):
    bucket.publish("2026-10-21+aaaaaaaa", edit=edit_examples(p_finish=0.123456))
    pull(root, url=bucket.url)
    bucket.publish("2026-10-28+bbbbbbbb", as_of="2026-10-28")
    pull(root, url=bucket.url)
    assert current(root) == "2026-10-28+bbbbbbbb"
    assert kept(root) == sorted([BAKED_VERSION, "2026-10-28+bbbbbbbb"])



# A version that was held isn't fetched again until something changes: a .held-<version> marker records the verdict.

def marker(root: Path, version: str = NEW) -> dict:
    return json.loads((root / f".held-{version}").read_text())


@pytest.mark.usefixtures("seeded")
def test_a_held_version_is_not_downloaded_again_but_is_logged_every_run(root, bucket, capsys):
    bucket.publish(NEW, edit=edit_examples(p_finish=0.123456))
    held(root, bucket, capsys, "example 3")
    for _ in range(2):
        bucket.requests.clear()
        capsys.readouterr()
        assert pull(root, url=bucket.url) == 0
        lines = [ln for ln in capsys.readouterr().err.splitlines() if f"held {NEW}" in ln]
        assert bucket.requests == ["/latest.json"]
        assert len(lines) == 1 and "example 3" in lines[0] and f"keeping {BAKED_VERSION}" in lines[0], lines
        assert_kept_out(root)


@pytest.mark.usefixtures("seeded")
def test_the_marker_records_the_digests_the_running_version_and_why(root, bucket, capsys):
    doc = bucket.publish(NEW, edit=edit_examples(p_finish=0.123456))
    held(root, bucket, capsys, "example 3")
    m = marker(root)
    assert (m["version"], m["files"], m["running"]) == (NEW, doc["files"], BAKED_VERSION)
    assert "example 3" in m["reason"]


@pytest.mark.usefixtures("seeded")
def test_a_version_published_again_with_other_digests_is_tried_again(root, bucket, capsys):
    bucket.publish(NEW, edit=edit_examples(p_finish=0.123456))
    held(root, bucket, capsys, "example 3")
    shutil.rmtree(bucket.root / NEW)
    bucket.publish(NEW)  # research fixed the files: latest.json's digests differ
    bucket.requests.clear()
    pull(root, url=bucket.url)
    assert any("examples.json" in r for r in bucket.requests)
    assert current(root) == NEW and not (root / f".held-{NEW}").exists()


@pytest.mark.usefixtures("seeded")
def test_a_checksum_mismatch_is_no_verdict_and_is_fetched_again(root, bucket, capsys):
    bucket.publish(NEW, corrupt="model.txt")  # the transfer, or the bucket, may be what broke: only a fetch can tell
    held(root, bucket, capsys, "model.txt: sha256")
    assert not list(root.glob(".held-*"))
    bucket.requests.clear()
    pull(root, url=bucket.url)
    assert any("model.txt" in r for r in bucket.requests)


def test_a_changed_running_version_makes_a_held_version_be_tried_again(root, bucket, capsys):
    bucket.publish("2026-10-21+aaaaaaaa", as_of="2026-10-21")
    pull(root, url=bucket.url)
    pull(root, url=bucket.url)
    later = "2026-10-28+bbbbbbbb"
    bucket.publish(later, as_of="2026-10-28", edit=edit_examples(p_finish=0.123456))
    held(root, bucket, capsys, "example 3", later, keeping="2026-10-21+aaaaaaaa")
    assert marker(root, later)["running"] == "2026-10-21+aaaaaaaa"
    pull(root, "--pin", BAKED_VERSION, url=bucket.url)  # the running model changes ...
    bucket.requests.clear()
    pull(root, url=bucket.url)  # ... and the pin is lifted
    assert any("examples.json" in r for r in bucket.requests)
    assert marker(root, later)["running"] == BAKED_VERSION  # judged again, against the model that now runs


@pytest.mark.usefixtures("seeded")
def test_a_pull_that_dies_in_the_gate_leaves_a_marker_that_stops_the_loop(root, bucket, capsys, monkeypatch):
    class Segfault(BaseException):  # lightgbm can end the process; nothing in-process catches that
        pass

    def die(bundle, examples):
        raise Segfault

    bucket.publish(NEW)
    monkeypatch.setattr(pull_module, "parity_failures", die)
    with pytest.raises(Segfault):
        pull(root, url=bucket.url)
    assert "gate" in marker(root)["reason"]
    monkeypatch.undo()
    capsys.readouterr()
    bucket.requests.clear()
    assert pull(root, url=bucket.url) == 0
    err = capsys.readouterr().err
    assert bucket.requests == ["/latest.json"] and f"held {NEW}" in err and "gate" in err, err
    assert current(root) == BAKED_VERSION and NEW not in kept(root)
    assert not list(root.glob(".tmp-*"))


@pytest.mark.usefixtures("seeded")
def test_an_exception_in_the_gate_is_a_hold_with_a_marker(root, bucket, capsys, monkeypatch):
    def broken(bundle, examples):
        raise RuntimeError("boom")

    bucket.publish(NEW)
    monkeypatch.setattr(pull_module, "parity_failures", broken)
    held(root, bucket, capsys, "boom")
    assert "boom" in marker(root)["reason"]


@pytest.mark.usefixtures("seeded")
def test_a_version_adopted_after_a_hold_leaves_no_marker_and_a_newer_latest_clears_the_old_one(root, bucket, capsys):
    bucket.publish("2026-10-21+aaaaaaaa", corrupt="model.txt")
    held(root, bucket, capsys, "sha256", "2026-10-21+aaaaaaaa")
    bucket.publish("2026-10-28+bbbbbbbb", as_of="2026-10-28")
    pull(root, url=bucket.url)
    pull(root, url=bucket.url)
    assert current(root) == "2026-10-28+bbbbbbbb"
    assert not list(root.glob(".held-*"))


@pytest.mark.usefixtures("seeded")
def test_a_failed_download_is_not_a_verdict_and_leaves_no_marker(root, bucket, capsys):
    bucket.publish(NEW)
    (bucket.root / NEW / "model.txt").rename(bucket.root / "model.txt.away")
    capsys.readouterr()
    pull(root, url=bucket.url)
    assert "failed, keeping" in capsys.readouterr().err and not list(root.glob(".held-*"))
    (bucket.root / "model.txt.away").rename(bucket.root / NEW / "model.txt")
    pull(root, url=bucket.url)
    assert current(root) == NEW


@pytest.mark.usefixtures("seeded")
def test_a_marker_that_cannot_be_read_is_ignored(root, bucket, capsys):
    bucket.publish(NEW, corrupt="model.txt")
    held(root, bucket, capsys, "sha256")
    (root / f".held-{NEW}").write_text("{not json")
    bucket.requests.clear()
    pull(root, url=bucket.url)
    assert any("model.txt" in r for r in bucket.requests)


# A latest.json that isn't a plain, known bundle is held before any file is fetched.

def bad_latest(bucket, **changes) -> dict:
    doc = bucket.publish(NEW, latest=False)
    write_latest(bucket.root, {**doc, **changes})
    return doc


@pytest.mark.parametrize("changes,reason", [
    ({"format_version": 2}, "format_version"),
    ({"format_version": None}, "format_version"),
    ({"version": "../escape"}, "version"),
    ({"version": "a/b"}, "version"),
    ({"version": "current"}, "version"),  # the symlink's own name
    ({"version": ""}, "version"),
    ({"version": 5}, "version"),
    ({"files": []}, "files"),
    ({"files": None}, "files"),
    ({"as_of": None}, "as_of"),
    ({"as_of": "yesterday"}, "as_of"),
    ({"as_of": 20261021}, "as_of"),
])
@pytest.mark.usefixtures("seeded")
def test_a_latest_that_is_not_the_published_shape_is_held(root, bucket, capsys, changes, reason):
    bad_latest(bucket, **changes)
    capsys.readouterr()
    bucket.requests.clear()
    assert pull(root, url=bucket.url) == 0
    assert "held" in capsys.readouterr().err
    assert bucket.requests == ["/latest.json"]
    assert_kept_out(root)
    assert not (root.parent / "escape").exists()


@pytest.mark.parametrize("name", ["../escape.txt", "sub/model.txt", "evil.sh", "README.md", ""])
@pytest.mark.usefixtures("seeded")
def test_a_file_name_outside_the_bundle_is_held_unfetched(root, bucket, capsys, name):
    doc = bucket.publish(NEW, latest=False)
    write_latest(bucket.root, {**doc, "files": {**doc["files"], name: "0" * 64}})
    capsys.readouterr()
    bucket.requests.clear()
    pull(root, url=bucket.url)
    assert "held" in capsys.readouterr().err
    assert bucket.requests == ["/latest.json"]
    assert_kept_out(root)


@pytest.mark.parametrize("missing", ["model.txt", "thresholds.json", "fighter_snapshot.parquet", "feature_spec.json",
                                     "manifest.json", "examples.json"])
@pytest.mark.usefixtures("seeded")
def test_a_bundle_without_a_required_file_is_held(root, bucket, capsys, missing):
    doc = bucket.publish(NEW, latest=False)
    write_latest(bucket.root, {**doc, "files": {n: h for n, h in doc["files"].items() if n != missing}})
    held(root, bucket, capsys, missing)


@pytest.mark.usefixtures("seeded")
def test_a_bundle_without_the_optional_rating_file_is_adopted(root, bucket):
    doc = bucket.publish(NEW, latest=False)
    write_latest(bucket.root, {**doc, "files": {n: h for n, h in doc["files"].items() if n != "fighter_rating.parquet"}})
    pull(root, url=bucket.url)
    pull(root, url=bucket.url)
    assert current(root) == NEW and not (root / NEW / "fighter_rating.parquet").exists()


# A bucket that can't be read is not a verdict on any model: log it, keep going.

@pytest.mark.parametrize("break_it", [
    lambda b: b.stop(),                                                  # connection refused
    lambda b: (b.root / "latest.json").unlink(),                         # 404
    lambda b: (b.root / "latest.json").write_text("{not json"),
    lambda b: (b.root / "latest.json").write_text("[]"),
    lambda b: (b.root / NEW / "model.txt").unlink(),                     # a file missing: 404 mid-download
])
@pytest.mark.usefixtures("seeded")
def test_an_unreachable_or_unreadable_bucket_keeps_the_current_model(root, bucket, capsys, break_it):
    bucket.publish(NEW)
    break_it(bucket)
    capsys.readouterr()
    assert pull(root, url=bucket.url) == 0
    assert "failed, keeping" in capsys.readouterr().err
    assert_kept_out(root)


@pytest.mark.usefixtures("seeded")
def test_a_file_over_the_size_cap_is_a_failure(root, bucket, capsys, monkeypatch):
    bucket.publish(NEW)
    monkeypatch.setattr(pull_module, "FILE_MAX", 1000)
    capsys.readouterr()
    assert pull(root, url=bucket.url) == 0
    assert "failed, keeping" in capsys.readouterr().err
    assert_kept_out(root)


@pytest.mark.parametrize("url", ["file:///etc", "ftp://example.com/models", "not a url", "models.xlobax.com"])
@pytest.mark.usefixtures("seeded")
def test_only_http_urls_are_fetched(root, capsys, url):
    assert pull(root, url=url) == 0
    assert "http" in capsys.readouterr().err
    assert current(root) == BAKED_VERSION  # the seed was still made


@pytest.mark.parametrize("target", ["ftp://127.0.0.1:9/latest.json", "file:///etc/hostname", "gopher://127.0.0.1:9/"])
@pytest.mark.usefixtures("seeded")
def test_a_redirect_to_a_scheme_other_than_http_is_refused(root, capsys, monkeypatch, target):
    import urllib.request
    opened = []
    monkeypatch.setattr(urllib.request.FTPHandler, "ftp_open", lambda self, req: opened.append(req.full_url))
    redirector = Redirector(target)
    try:
        capsys.readouterr()
        assert pull(root, url=redirector.url) == 0
    finally:
        redirector.stop()
    err = capsys.readouterr().err
    assert opened == []  # never followed
    assert "failed, keeping" in err and ("not an http(s) url" in err or "is not allowed" in err), err  # urllib's own
    assert current(root) == BAKED_VERSION


def test_a_redirect_to_another_http_url_is_followed(root, bucket):
    bucket.publish(NEW)
    redirector = Redirector(bucket.url + "/latest.json")
    try:
        pull(root, url=redirector.url)  # the redirector sends latest.json (and so every file) to the real one
    finally:
        redirector.stop()
    assert any(r == "/latest.json" for r in bucket.requests)


def test_every_request_names_fightcard_as_its_user_agent(root, bucket):
    # Cloudflare answers 403 to urllib's default "Python-urllib/3.x" (models.xlobax.com did, on the first staging pull)
    bucket.publish(NEW)
    pull(root, url=bucket.url)
    assert current(root) == NEW
    assert bucket.user_agents and all(ua.startswith("fightcard") for ua in bucket.user_agents)


# FIGHTCARD_MODEL_PIN: run a kept version and pull nothing, which is also how a bad model is rolled back.

@pytest.fixture
def two(root, bucket):
    """The baked version and NEW both kept, running NEW."""
    bucket.publish(NEW)
    pull(root, url=bucket.url)
    pull(root, url=bucket.url)
    assert current(root) == NEW and kept(root) == sorted([BAKED_VERSION, NEW])
    bucket.requests.clear()


def test_a_pin_rolls_back_to_a_kept_version_without_touching_the_bucket(root, bucket, two, capsys):
    capsys.readouterr()
    assert pull(root, "--pin", BAKED_VERSION, url=bucket.url) == 0
    assert current(root) == BAKED_VERSION
    assert kept(root) == sorted([BAKED_VERSION, NEW])  # the version rolled back from stays kept
    assert bucket.requests == []
    assert f"pinned {BAKED_VERSION} (was {NEW})" in capsys.readouterr().err


def test_a_pin_holds_while_the_bucket_has_a_newer_version(root, bucket, two):
    pull(root, "--pin", BAKED_VERSION, url=bucket.url)
    bucket.publish("2026-10-28+bbbbbbbb", as_of="2026-10-28")
    pull(root, "--pin", BAKED_VERSION, url=bucket.url)
    assert current(root) == BAKED_VERSION
    assert bucket.requests == []


def test_unpinning_resumes_pulling(root, bucket, two):
    pull(root, "--pin", BAKED_VERSION, url=bucket.url)
    assert pull(root, url=bucket.url) == 0  # no pin: latest.json (NEW) differs from current, and NEW is still kept
    assert current(root) == NEW


def test_pinning_the_running_version_changes_nothing(root, bucket, two, capsys):
    capsys.readouterr()
    assert pull(root, "--pin", NEW, url=bucket.url) == 0
    assert current(root) == NEW and bucket.requests == []
    assert "already" in capsys.readouterr().err


@pytest.mark.parametrize("pin", ["2030-01-01+ffffffff", "../model", "a/b", "current"])
def test_a_pin_that_is_not_a_kept_version_is_held(root, bucket, two, capsys, pin):
    capsys.readouterr()
    assert pull(root, "--pin", pin, url=bucket.url) == 0
    assert current(root) == NEW and bucket.requests == []
    err = capsys.readouterr().err
    assert "not a kept version" in err and f"keeping {NEW}" in err


def test_a_pin_that_cannot_be_honoured_is_an_error_on_every_run(root, bucket, two, capsys):
    capsys.readouterr()
    for _ in range(2):
        assert pull(root, "--pin", "2030-01-01+ffffffff", url=bucket.url) == 0  # still never fails the run
        lines = [ln for ln in capsys.readouterr().err.splitlines() if "2030-01-01+ffffffff" in ln]
        assert len(lines) == 1 and "ERROR" in lines[0] and "FIGHTCARD_MODEL_PIN" in lines[0], lines
        assert current(root) == NEW


def test_a_pin_to_an_unusable_kept_version_is_an_error_too(root, bucket, two, capsys):
    (root / BAKED_VERSION / "model.txt").write_text("garbage")
    capsys.readouterr()
    pull(root, "--pin", BAKED_VERSION, url=bucket.url)
    assert "ERROR" in capsys.readouterr().err


def test_a_pin_to_a_kept_version_that_no_longer_loads_is_held(root, bucket, two, capsys):
    (root / BAKED_VERSION / "model.txt").write_text("garbage")
    capsys.readouterr()
    assert pull(root, "--pin", BAKED_VERSION, url=bucket.url) == 0
    assert current(root) == NEW
    assert "unusable" in capsys.readouterr().err


def test_a_pin_works_without_a_url(root, bucket, two):
    assert pull(root, "--pin", BAKED_VERSION) == 0
    assert current(root) == BAKED_VERSION


def test_the_pin_defaults_to_the_environment(root, bucket, two, monkeypatch):
    monkeypatch.setenv("FIGHTCARD_MODEL_PIN", BAKED_VERSION)
    assert pull(root, url=bucket.url) == 0
    assert current(root) == BAKED_VERSION and bucket.requests == []


def test_a_pin_on_the_first_boot_can_name_the_baked_version(root, bucket):
    assert pull(root, "--pin", BAKED_VERSION, url=bucket.url) == 0
    assert current(root) == BAKED_VERSION and bucket.requests == []


def test_the_pinned_version_is_never_pruned(root, bucket, two):
    bucket.publish("2026-10-28+bbbbbbbb", as_of="2026-10-28")
    pull(root, url=bucket.url)
    pull(root, "--pin", BAKED_VERSION, "--keep", "1", url=bucket.url)
    assert current(root) == BAKED_VERSION
    assert kept(root) == sorted([BAKED_VERSION, "2026-10-28+bbbbbbbb"])  # the newest, and the pin
