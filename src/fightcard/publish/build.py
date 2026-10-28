"""One /v1/ generation built from data/, as `{published path: bytes}`.

data/ is validated with the pydantic models first; anything a consumer couldn't rely on raises DataError
and nothing is built. JSON is re-serialised with the contract's formatting rule (store.dumps), and the
snapshot is byte-for-byte deterministic, so identical input gives identical files.
"""

from __future__ import annotations

import gzip
import io
import json
import re
import sys
import tarfile
from pathlib import Path

from pydantic import ValidationError
from pydantic.json_schema import models_json_schema

from fightcard import models
from fightcard.models import Event, Fighter, Rankings
from fightcard.store import dumps

CONTRACT_VERSION = "1.3"
SCHEMA_ID = "https://ufc-data.xlobax.com/v1/schema.json"
IMAGE_SUFFIXES = {".webp", ".png", ".jpg", ".jpeg", ".avif", ".gif"}
SNAPSHOT = "snapshot.tar.gz"
_UTC_Z = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z")


class DataError(Exception):
    """data/ is missing, unreadable or invalid; don't publish."""


class Index(models.Index):
    """/v1/index.json: data/index.json plus the contract version."""

    contract_version: str


def _read(path: Path, data_dir: Path):
    rel = path.relative_to(data_dir).as_posix()
    try:
        return rel, json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise DataError(f"{rel} is missing") from e
    except (OSError, ValueError) as e:
        raise DataError(f"cannot read {rel}: {e}") from e


def _validate(model, rel: str, data):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise DataError(f"{rel} is invalid: {e}") from e


def _json(model) -> bytes:
    return dumps(model.model_dump(mode="json")).encode("utf-8")


def _load_index(data_dir: Path) -> Index:
    rel, data = _read(data_dir / "index.json", data_dir)
    if not isinstance(data, dict):
        raise DataError(f"{rel} is invalid: not an object")
    index = _validate(Index, rel, {**data, "contract_version": CONTRACT_VERSION})
    if not _UTC_Z.fullmatch(index.generated_at):
        raise DataError(f"{rel}: generated_at {index.generated_at!r} is not ISO 8601 UTC ending in Z")
    return index


def _load_events(data_dir: Path) -> dict[str, Event]:
    events = {}
    for path in sorted((data_dir / "events").glob("*.json")):
        rel, data = _read(path, data_dir)
        event = _validate(Event, rel, data)
        if event.id != path.stem:
            raise DataError(f"{rel} holds event {event.id!r}")
        events[event.id] = event
    return events


def _load_fighters(data_dir: Path) -> dict[str, Fighter]:
    rel, data = _read(data_dir / "fighters.json", data_dir)
    if not isinstance(data, dict):
        raise DataError(f"{rel} is invalid: not an object")
    fighters = {}
    for fid, raw in data.items():
        fighter = _validate(Fighter, f"{rel}[{fid}]", raw)
        if fighter.id != fid:
            raise DataError(f"{rel}: fighter {fighter.id!r} is filed under {fid!r}")
        fighters[fid] = fighter
    return fighters


def _images(data_dir: Path) -> dict[str, bytes]:
    images = data_dir / "images"
    if not images.is_dir():
        return {}
    return {f"images/{p.name}": p.read_bytes() for p in sorted(images.iterdir())
            if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in IMAGE_SUFFIXES}


def _load_rankings(data_dir: Path, images: dict[str, bytes]) -> Rankings | None:
    """data/rankings.json if there is one (it's optional): valid, with a P4P `image` that isn't among the published
    `images` published as null (and a warning), so one missing headshot doesn't hold back the generation."""
    if not (data_dir / "rankings.json").exists():
        return None
    rel, data = _read(data_dir / "rankings.json", data_dir)
    rankings = _validate(Rankings, rel, data)
    absent = [e for es in (rankings.p4p.men, rankings.p4p.women) for e in es
              if e.image is not None and e.image not in images]
    if absent:
        print(f"publish: WARNING: {rel}: P4P images that are not among the published images, published as null: "
              f"{', '.join(f'{e.id} ({e.image})' for e in absent)}", file=sys.stderr)
        for e in absent:
            e.image = None
    return rankings


def schema() -> dict:
    """JSON Schema for the published JSON: `#/$defs/Index`, `Event`, `Fighters` (fighters.json), `Fighter`,
    `Rankings`."""
    _, top = models_json_schema([(m, "validation") for m in (Index, Event, Fighter, Rankings)],
                                ref_template="#/$defs/{model}")
    defs = top["$defs"]
    defs["Fighters"] = {"type": "object", "additionalProperties": {"$ref": "#/$defs/Fighter"}}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": f"fightcard /v1 (contract {CONTRACT_VERSION})",
        "description": "index.json is #/$defs/Index, events/{id}.json is #/$defs/Event, "
                       "fighters.json is #/$defs/Fighters, rankings.json is #/$defs/Rankings.",
        "$defs": defs,
    }


def _tarinfo(name: str, size: int | None) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    if size is None:
        info.type, info.mode = tarfile.DIRTYPE, 0o755
    else:
        info.size, info.mode = size, 0o644
    info.mtime = info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def snapshot(files: dict[str, bytes]) -> bytes:
    """files as a .tar.gz with sorted entries, zero mtimes and owners, and no gzip timestamp or name."""
    dirs = {"/".join(name.split("/")[:i]) for name in files for i in range(1, name.count("/") + 1)}
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name in sorted(dirs | set(files)):
                if name in dirs:
                    tar.addfile(_tarinfo(name, None))
                else:
                    tar.addfile(_tarinfo(name, len(files[name])), io.BytesIO(files[name]))
    return buf.getvalue()


def build(data_dir: Path) -> dict[str, bytes]:
    """Every /v1/ file for data_dir. Raises DataError if data/ isn't fit to publish."""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise DataError(f"{data_dir} is not a directory")
    index = _load_index(data_dir)
    events = _load_events(data_dir)
    fighters = _load_fighters(data_dir)
    images = _images(data_dir)
    rankings = _load_rankings(data_dir, images)
    missing = [eid for eid in index.upcoming + index.recent if eid not in events]
    if missing:
        raise DataError(f"index.json names events with no events/<id>.json: {', '.join(missing)}")
    unknown = [f"{fight.id} ({fid})" for event in events.values() for fight in event.fights
               for fid in (fight.red, fight.blue) if fid not in fighters]
    if unknown:
        raise DataError(f"fights name fighters missing from fighters.json: {', '.join(unknown)}")

    files = {"index.json": _json(index), "schema.json": dumps(schema()).encode("utf-8"),
             "fighters.json": dumps({fid: f.model_dump(mode="json") for fid, f in fighters.items()}).encode("utf-8")}
    files.update({f"events/{eid}.json": _json(event) for eid, event in events.items()})
    if rankings is not None:
        files["rankings.json"] = _json(rankings)
    files.update(images)
    files[SNAPSHOT] = snapshot(files)
    return files
