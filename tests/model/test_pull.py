"""`fightcard model pull`: the engine adopts the model research publishes (docs/SERVICE.md), against a fake bucket."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from model_bucket import BAKED_VERSION, BUNDLE, MODEL_DIR, sha256

from fightcard.cli import main
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


def test_a_latest_that_points_back_at_an_older_version_rolls_back(root, bucket):
    bucket.publish(NEW)
    pull(root, url=bucket.url)
    assert current(root) == NEW
    bucket.publish(BAKED_VERSION)  # research re-pointed latest.json at the previous version
    assert pull(root, url=bucket.url) == 0
    assert current(root) == BAKED_VERSION


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
