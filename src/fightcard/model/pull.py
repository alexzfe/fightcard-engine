"""The model state dir, <FIGHTCARD_ROOT>/state/model, and how `fightcard model pull` keeps it:

    <root>/<version>/      a bundle (the files `python -m export` writes), never changed once adopted
    <root>/current         relative symlink -> <version>; `score --model-dir` reads it

The first run with a pull enabled seeds the first version from the image's baked model/. A new version replaces
`current` only after every gate in `_gate` passes; one that is held (or ends the process in the gate) leaves
`.held-<version>`, a note of the verdict, so later runs don't fetch and load it again until something changes; `current` is switched by renaming a new symlink over it (the
way `publish` switches its own), so a reader sees the old model or the new one. Any failure keeps `current`.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fightcard.score.bundle import FILES, load_bundle
from fightcard.score.parity import parity_failures

KEEP = 3
FORMAT_VERSION = 1  # latest.json's
CUT_POINTS = ("low_max", "medium_max")  # thresholds.json: the tier boundaries a new model must keep
EXAMPLES = "examples.json"
BUNDLE_FILES = (*FILES, EXAMPLES)  # a bundle must have these
OPTIONAL_FILES = ("fighter_rating.parquet",)  # and may have these
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}")  # a directory name: never a path
RESERVED = "current"  # the symlink's name: no version may take it
SHA256 = re.compile(r"[0-9a-f]{64}")
_STAGING, _LINK, _HELD = ".tmp-", ".current-", ".held-"
_SCHEMES = ("http", "https")
TIMEOUT = 30  # seconds, per socket operation
LATEST_MAX = 1 << 20
FILE_MAX = 256 << 20

Log = Callable[[str], None]


class Failed(Exception):
    """The bucket couldn't be read (down, 404, cut off): nothing to judge; the next run asks again."""


class Held(Exception):
    """A version was read and rejected: `label` is what the log calls it, `str(self)` why. Current stays."""

    def __init__(self, label: str, reason: str):
        super().__init__(reason)
        self.label = label


@dataclass(frozen=True)
class Latest:
    version: str
    as_of: date
    files: dict[str, str]  # name -> sha256 hex


def _is_version(value: object) -> bool:
    return isinstance(value, str) and VERSION.fullmatch(value) is not None and value != RESERVED


def _as_of(value: object) -> date | None:
    """A YYYY-MM-DD string as a date; None for anything else."""
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _running_as_of(root: Path, version: str) -> date | None:
    try:
        return _as_of(json.loads((root / version / "manifest.json").read_text()).get("as_of"))
    except (OSError, ValueError, AttributeError):
        return None


def _kept_current(root: Path) -> str | None:
    """The version `current` points at, or None when there is no current or its version was removed."""
    link = root / "current"
    if not link.is_symlink():
        return None
    version = os.readlink(link)
    return version if _is_version(version) and (root / version).is_dir() else None


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


def _stage_seed(root: Path, seed_dir: Path) -> Path:
    staging = root / f"{_STAGING}seed"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    for name in (*BUNDLE_FILES, *OPTIONAL_FILES):
        if (seed_dir / name).is_file():
            shutil.copy2(seed_dir / name, staging / name)
    return staging


def _seed(root: Path, seed_dir: Path, log: Log) -> bool:
    """Ensure `current` is a usable kept version, seeding the baked model when it isn't. False: it still isn't."""
    version = _kept_current(root)
    if version is not None and _usable(root, version):
        return True
    try:
        baked = load_bundle(seed_dir).manifest["version"]
        if not _is_version(baked):
            raise ValueError(f"unusable version {baked!r}")
    except Exception as e:  # noqa: BLE001
        log(f"can't seed current from {seed_dir}: {e}")
        return False
    staging = _stage_seed(root, seed_dir)
    _install(root, baked, staging)
    _switch(root, baked)
    log(f"seeded current with the baked model {baked} from {seed_dir}")
    return True


def _newer_baked(root: Path, seed_dir: Path, was: str, switch: bool, log: Log) -> None:
    """An image with a newer baked model than `current` (an upgrade; the cut points may be new) makes it the kept
    version, through the pull's gates except the cut-point check; `switch` False (a pin) only keeps it."""
    try:
        manifest = json.loads((seed_dir / "manifest.json").read_text())
        baked, as_of = manifest["version"], _as_of(manifest.get("as_of"))
    except (OSError, ValueError, KeyError, TypeError):
        return  # _seed already said so, when there was nothing else to run
    if not _is_version(baked) or baked == was:
        return
    running = _running_as_of(root, was)
    if as_of is None or (running is not None and as_of <= running):
        log(f"baked model {baked} (as_of {as_of}) is not newer than current {was} (as_of {running}); keeping current")
        return
    try:
        if baked not in _versions(root) or not _usable(root, baked):
            staging = _stage_seed(root, seed_dir)
            try:
                _gate(root, baked, staging, was, cut_points=False)
                _install(root, baked, staging)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        if switch:
            _switch(root, baked)
            log(f"adopted the baked model {baked} (was {was}): the image's is newer")
        else:
            log(f"kept the baked model {baked}: the image's is newer than {was}, and a pin is set")
    except Held as e:
        log(f"held baked {e.label}: {e}; keeping {was}")


def _versions(root: Path) -> list[str]:
    """The kept versions, oldest first (they start with their as_of date)."""
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink() and _is_version(p.name))


def _prune(root: Path, keep: int) -> None:
    """Delete all but the newest `keep` versions (they sort by as_of), and never the one `current` points at."""
    versions = _versions(root)
    live = _kept_current(root)
    for version in versions[:max(len(versions) - keep, 0)]:
        if version != live:
            shutil.rmtree(root / version)


class _HttpOnly(urllib.request.HTTPRedirectHandler):
    """urllib follows a redirect to ftp: as well; a bucket only ever answers over http(s)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme not in _SCHEMES:
            raise Failed(f"{req.full_url} redirects to {newurl}, which is not an http(s) url")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpOnly)


def _read(url: str, sink: Callable[[bytes], None], limit: int) -> None:
    """Stream `url` into `sink`; anything that goes wrong on the way is a Failed."""
    if urllib.parse.urlsplit(url).scheme not in _SCHEMES:
        raise Failed(f"{url} is not an http(s) url")
    try:
        with _OPENER.open(url, timeout=TIMEOUT) as response:
            size = 0
            while chunk := response.read(1 << 16):
                size += len(chunk)
                if size > limit:
                    raise Failed(f"{url} is over {limit} bytes")
                sink(chunk)
    except (OSError, http.client.HTTPException, ValueError) as e:  # URLError and HTTPError are OSErrors
        raise Failed(f"{url}: {e}") from e


def _latest(base: str) -> Latest:
    """latest.json: {"format_version": 1, "version", "as_of", "files": {name: sha256}}. Unreadable is a Failed; a
    readable one that isn't a plain bundle of known files is Held, before anything it names is fetched."""
    body = bytearray()
    _read(f"{base}/latest.json", body.extend, LATEST_MAX)
    try:
        doc = json.loads(body)
    except ValueError as e:
        raise Failed(f"{base}/latest.json is not JSON: {e}") from e
    if not isinstance(doc, dict):
        raise Failed(f"{base}/latest.json is not an object")
    version, files = doc.get("version"), doc.get("files")
    label = version if _is_version(version) else "latest.json"
    if doc.get("format_version") != FORMAT_VERSION:
        raise Held(label, f"latest.json has format_version {doc.get('format_version')!r}, not {FORMAT_VERSION}")
    if label == "latest.json":
        raise Held(label, f"latest.json has an unusable version {version!r}")
    as_of = _as_of(doc.get("as_of"))
    if as_of is None:
        raise Held(label, f"latest.json has an unusable as_of {doc.get('as_of')!r}")
    if not isinstance(files, dict):
        raise Held(label, "latest.json's files is not an object")
    allowed = {*BUNDLE_FILES, *OPTIONAL_FILES}
    for name, digest in files.items():
        if name not in allowed:
            raise Held(label, f"latest.json lists unexpected file {name!r}")
        if not (isinstance(digest, str) and SHA256.fullmatch(digest)):
            raise Held(label, f"latest.json's digest for {name} is not a sha256")
    if missing := [n for n in BUNDLE_FILES if n not in files]:
        raise Held(label, f"latest.json lacks required {', '.join(missing)}")
    return Latest(version, as_of, files)


def _no_downgrade(label: str, as_of: date | None, running: date | None, was: str, source: str) -> None:
    """An older export than the running model's is never adopted; going back is FIGHTCARD_MODEL_PIN's job."""
    if as_of is None:
        raise Held(label, f"{source} has no usable as_of")
    if running is not None and as_of < running:
        raise Held(label, f"{source} as_of {as_of} is older than {was}'s {running}; to go back, "
                          f"set FIGHTCARD_MODEL_PIN to a kept version")


def _note(root: Path, latest: Latest, was: str, reason: str) -> None:
    """Record that `latest` is held (or went into the gate and didn't come out) while `was` is running."""
    note = root / f"{_HELD}{latest.version}"
    tmp = root / f"{_STAGING}held-{latest.version}"
    tmp.write_text(json.dumps({"version": latest.version, "files": latest.files, "running": was, "reason": reason}))
    os.replace(tmp, note)


def _held_reason(root: Path, latest: Latest, was: str) -> str | None:
    """Why `latest` was held before, when nothing it was judged on has changed since: same files, same running model."""
    try:
        note = json.loads((root / f"{_HELD}{latest.version}").read_text())
        same = (note["version"], note["files"], note["running"]) == (latest.version, latest.files, was)
        return note["reason"] if same and isinstance(note["reason"], str) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _forget(root: Path, *, except_version: str | None = None) -> None:
    for note in root.glob(f"{_HELD}*"):
        if note.name != f"{_HELD}{except_version}":
            note.unlink(missing_ok=True)


def _download(base: str, latest: Latest, staging: Path) -> None:
    quoted = urllib.parse.quote(latest.version, safe="")
    for name, want in sorted(latest.files.items()):
        digest = hashlib.sha256()
        with open(staging / name, "wb") as out:
            def sink(chunk: bytes) -> None:
                digest.update(chunk)
                out.write(chunk)
            _read(f"{base}/{quoted}/{urllib.parse.quote(name)}", sink, FILE_MAX)
        if digest.hexdigest() != want:
            raise Held(latest.version, f"{name}: sha256 {digest.hexdigest()[:12]}… is not the published {want[:12]}…")


def _gate(root: Path, label: str, staging: Path, was: str, *, cut_points: bool = True) -> None:
    """The candidate `label` in `staging` (every checksum already matched) must be the version it says, load, keep
    the running model's tier cut points (the image's own baked model is exempt: it defines them), and re-score its
    own examples."""
    try:
        manifest = json.loads((staging / "manifest.json").read_text())
        listed = manifest["version"]
    except (ValueError, KeyError, TypeError) as e:
        raise Held(label, f"manifest.json is unreadable: {e}") from e
    if listed != label:
        raise Held(label, f"manifest.json is for version {listed!r}")
    _no_downgrade(label, _as_of(manifest.get("as_of")), _running_as_of(root, was), was, "manifest.json")
    try:
        bundle = load_bundle(staging)
    except Exception as e:  # noqa: BLE001 - lightgbm, pyarrow and json each raise their own
        raise Held(label, f"load_bundle failed: {type(e).__name__}: {e}") from e
    if cut_points:
        running = load_bundle(root / was).thresholds
        for cut in CUT_POINTS:
            if bundle.thresholds.get(cut) != running[cut]:
                raise Held(label, f"{cut} moved from {running[cut]!r} to {bundle.thresholds.get(cut)!r}")
    try:
        examples = json.loads((staging / EXAMPLES).read_text())
    except ValueError as e:
        raise Held(label, f"{EXAMPLES} is not JSON: {e}") from e
    if failures := parity_failures(bundle, examples):
        raise Held(label, "; ".join(failures[:3]))


def _adopt(root: Path, base: str, latest: Latest, was: str, log: Log) -> None:
    _no_downgrade(latest.version, latest.as_of, _running_as_of(root, was), was, "latest.json")
    if reason := _held_reason(root, latest, was):
        raise Held(latest.version, f"{reason} (not fetched again: same files, same running model)")
    staging = root / f"{_STAGING}{latest.version}"
    try:
        staging.mkdir()
        _download(base, latest, staging)  # a checksum mismatch is no verdict (the transfer may be what broke): no note
        _note(root, latest, was, "the pull ended inside the gate (a crash?)")  # stays if the process doesn't come back
        try:
            _gate(root, latest.version, staging, was)
        except Held as e:
            _note(root, latest, was, str(e))
            raise
        except Exception as e:  # noqa: BLE001 - whatever a candidate makes the gate raise is a verdict on it
            reason = f"the gate raised {type(e).__name__}: {e}"
            _note(root, latest, was, reason)
            raise Held(latest.version, reason) from e
        _forget(root)
        _install(root, latest.version, staging)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    _switch(root, latest.version)
    log(f"adopted {latest.version} (was {was})")


def _pin(root: Path, pin: str, was: str, log: Log) -> None:
    """Run the kept version `pin`: no gates, since it passed them when it was adopted (or is the baked model)."""
    versions = _versions(root)
    if pin not in versions:
        log(f"ERROR FIGHTCARD_MODEL_PIN {pin!r} is not a kept version (kept: {', '.join(versions)}); keeping {was}")
    elif pin == was:
        log(f"pin {pin} is already current")
    elif not _usable(root, pin):
        log(f"ERROR FIGHTCARD_MODEL_PIN {pin!r} is an unusable kept version (it doesn't load); keeping {was}")
    else:
        _switch(root, pin)
        log(f"pinned {pin} (was {was})")


def pull(root: Path, *, url: str | None, pin: str | None, seed_dir: Path, keep: int = KEEP, log: Log) -> None:
    """One pull. Never raises for a model that can't be adopted: it logs why and leaves `current` as it was."""
    if not url and not pin:
        log("disabled: FIGHTCARD_MODEL_URL is not set")
        return
    root.mkdir(parents=True, exist_ok=True)
    for leftover in root.glob(f"{_STAGING}*"):
        shutil.rmtree(leftover, ignore_errors=True)
    for leftover in root.glob(f"{_LINK}*"):  # a switch killed between symlink() and os.replace()
        leftover.unlink(missing_ok=True)
    if not _seed(root, seed_dir, log):
        return
    was = _kept_current(root)
    _newer_baked(root, seed_dir, was, switch=not pin, log=log)
    was = _kept_current(root)
    if pin:
        _pin(root, pin, was, log)
        _prune(root, keep)
        return
    base = url.rstrip("/")
    try:
        latest = _latest(base)
        _forget(root, except_version=latest.version)  # a verdict on a version latest.json has moved on from
        if latest.version == was:
            log(f"up to date: {was}")
        else:
            _adopt(root, base, latest, was, log)
    except Held as e:
        log(f"held {e.label}: {e}; keeping {was}")
    except Failed as e:
        log(f"failed, keeping {was}: {e}")
    _prune(root, keep)
