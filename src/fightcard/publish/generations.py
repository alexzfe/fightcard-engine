"""Atomic publishing under a root nginx serves from:

    <root>/generations/<id>/   one complete build per publish, never changed once written
    <root>/current             relative symlink -> generations/<id>

A generation is written in full under generations/.tmp-<id>/, renamed into place, and only then is `current`
switched by renaming a new symlink over it. rename() is atomic, so a reader resolving `current` gets the old
generation or the new one, never a half-written tree. A failure before the switch leaves `current` as it was.

Files identical to the live generation's are hard links to them: they keep their inode and mtime, so nginx
keeps their ETag and the old generations cost no extra disk. Identical content publishes nothing.

One publisher at a time per root: a second run would clear the first one's staging directory.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fightcard.publish.build import SNAPSHOT

KEEP = 5
_STAGING, _LINK = ".tmp-", ".current-"


@dataclass(frozen=True)
class Generation:
    id: str
    changed: bool  # False: identical to the live generation, nothing written


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(files: dict[str, bytes]) -> str:
    return hashlib.sha256(files[SNAPSHOT]).hexdigest()[:12]  # the snapshot holds every other file


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o755)


def _clear_leftovers(root: Path, gens: Path) -> None:
    for p in gens.glob(f"{_STAGING}*"):
        shutil.rmtree(p)
    for p in root.glob(f"{_LINK}*"):
        p.unlink()


def _write(files: dict[str, bytes], staging: Path, live: Path | None) -> None:
    _mkdir(staging)
    for name in sorted(files):
        path = staging / name
        _mkdir(path.parent)
        old = live / name if live else None
        if old is not None and old.is_file() and old.read_bytes() == files[name]:
            os.link(old, path)
            continue
        path.write_bytes(files[name])
        path.chmod(0o644)
        if old is not None and old.is_file():
            _later_than(path, old)


def _later_than(path: Path, old: Path) -> None:
    """nginx's ETag is "<mtime seconds>-<size>": a same-size rewrite in the same second as the file it replaces
    (or after the clock stepped back) would repeat its ETag, and clients would revalidate stale content to 304."""
    floor = int(old.stat().st_mtime) + 1
    if int(path.stat().st_mtime) < floor:
        os.utime(path, (floor, floor))


def _prune(gens: Path, keep: int, live_id: str) -> None:
    ids = sorted(p.name for p in gens.iterdir() if not p.name.startswith("."))
    for gid in ids[:-keep]:
        if gid != live_id:
            shutil.rmtree(gens / gid)


def publish(files: dict[str, bytes], root: Path, *, keep: int = KEEP,
            now: Callable[[], datetime] = _now) -> Generation:
    """Make files the live generation under root, keeping the newest `keep` generations."""
    root = Path(root)
    gens = root / "generations"
    _mkdir(root)
    _mkdir(gens)
    _clear_leftovers(root, gens)

    current = root / "current"
    live = current.resolve() if current.is_symlink() else None
    if live is not None and not live.is_dir():
        live = None  # dangling: its generation was removed
    digest = _digest(files)
    if live is not None and live.name.endswith(f"-{digest}"):
        return Generation(live.name, changed=False)

    gen_id = f"{now():%Y%m%dT%H%M%S%fZ}-{digest}"
    staging = gens / f"{_STAGING}{gen_id}"
    try:
        _write(files, staging, live)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    staging.rename(gens / gen_id)

    link = root / f"{_LINK}{gen_id}"
    link.symlink_to(f"generations/{gen_id}")
    os.replace(link, current)
    _prune(gens, keep, gen_id)
    return Generation(gen_id, changed=True)
