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
