"""The model state dir, <FIGHTCARD_ROOT>/state/model, and how `fightcard model pull` keeps it:

    <root>/<version>/      a bundle (the files `python -m export` writes), never changed once adopted
    <root>/current         relative symlink -> <version>; `score --model-dir` reads it

The first run with a pull enabled seeds the first version from the image's baked model/. A new version replaces
`current` only after every gate in `adopt` passes; `current` is switched by renaming a new symlink over it (the
way `publish` switches its own), so a reader sees the old model or the new one. Any failure keeps `current`.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from fightcard.score.bundle import FILES, load_bundle

KEEP = 3
EXAMPLES = "examples.json"
BUNDLE_FILES = (*FILES, EXAMPLES)  # a bundle must have these
OPTIONAL_FILES = ("fighter_rating.parquet",)  # and may have these
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}")  # a directory name: never a path
_STAGING, _LINK = ".tmp-", ".current-"

Log = Callable[[str], None]


def _kept_current(root: Path) -> str | None:
    """The version `current` points at, or None when there is no current or its version was removed."""
    link = root / "current"
    if not link.is_symlink():
        return None
    version = os.readlink(link)
    return version if VERSION.fullmatch(version) and (root / version).is_dir() else None


def _usable(root: Path, version: str) -> bool:
    try:
        load_bundle(root / version)
    except Exception:  # noqa: BLE001 - lightgbm, pyarrow and json each raise their own
        return False
    return True


def _install(root: Path, version: str, files: Path) -> None:
    """Make `files` (a complete directory, moved) the kept `version`, replacing a copy that was already there."""
    dest = root / version
    if dest.exists():
        shutil.rmtree(dest)
    files.rename(dest)


def _switch(root: Path, version: str) -> None:
    link = root / f"{_LINK}{os.getpid()}"
    link.unlink(missing_ok=True)
    link.symlink_to(version)
    os.replace(link, root / "current")


def _seed(root: Path, seed_dir: Path, log: Log) -> bool:
    """Ensure `current` is a usable kept version, seeding the baked model when it isn't. False: it still isn't."""
    version = _kept_current(root)
    if version is not None and _usable(root, version):
        return True
    try:
        baked = load_bundle(seed_dir).manifest["version"]
        if not (isinstance(baked, str) and VERSION.fullmatch(baked)):
            raise ValueError(f"unusable version {baked!r}")
    except Exception as e:  # noqa: BLE001
        log(f"can't seed current from {seed_dir}: {e}")
        return False
    staging = root / f"{_STAGING}seed"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    for name in (*BUNDLE_FILES, *OPTIONAL_FILES):
        if (seed_dir / name).is_file():
            shutil.copy2(seed_dir / name, staging / name)
    _install(root, baked, staging)
    _switch(root, baked)
    log(f"seeded current with the baked model {baked} from {seed_dir}")
    return True


def pull(root: Path, *, url: str | None, pin: str | None, seed_dir: Path, keep: int = KEEP, log: Log) -> None:
    """One pull. Never raises for a model that can't be adopted: it logs why and leaves `current` as it was."""
    if not url and not pin:
        log("disabled: FIGHTCARD_MODEL_URL is not set")
        return
    root.mkdir(parents=True, exist_ok=True)
    for leftover in root.glob(f"{_STAGING}*"):
        shutil.rmtree(leftover, ignore_errors=True)
    _seed(root, seed_dir, log)
