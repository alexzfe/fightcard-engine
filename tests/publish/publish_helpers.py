"""A small valid data/ tree for the publish stage, and readers for what it publishes."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from fightcard.models import Event, Fight, Fighter, Index, Record
from fightcard.store import Store, write_json

GENERATED_AT = "2026-10-25T23:20:24Z"
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 fake-headshot-f1"
PNG = b"\x89PNG\r\n\x1a\nfake-headshot-f2"


def make_data(root: Path, generated_at: str = GENERATED_AT) -> Path:
    store = Store(root)
    store.save_fighters({
        "f1": Fighter(id="f1", name="Ana Red", record=Record(w=10, l=2), image="images/f1.webp", elo=1612),
        "f2": Fighter(id="f2", name="Bea Blue", record=Record(w=8, l=3), image="images/f2.png"),
    })
    store.save_event(Event(id="e1", slug="ufc-1", name="UFC 1", date="2026-11-01", status="upcoming",
                           fights=[Fight(id="e1-1", red="f1", blue="f2", finish_tier="high", segment="main")]))
    store.save_event(Event(id="e2", slug="ufc-0", name="UFC 0", date="2026-10-18", status="completed", fights=[]))
    store.save_index(Index(generated_at=generated_at, upcoming=["e1"], recent=["e2"]))
    images = root / "images"
    write_json(images / "misses.json", {"f9": "2026-10-20T00:00:00Z"})
    (images / "f1.webp").write_bytes(WEBP)
    (images / "f2.png").write_bytes(PNG)
    return root


def snapshot_members(blob: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}


def tree(root: Path) -> dict[str, bytes]:
    """Every file under root by relative POSIX path."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
