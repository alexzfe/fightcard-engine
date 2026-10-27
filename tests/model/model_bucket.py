"""A fake model bucket: what FIGHTCARD_MODEL_URL points at (docs/SERVICE.md), served from a tmp dir by a local HTTP
server, so no test touches the network or the real bucket.

    <bucket>/latest.json                  {"format_version", "version", "as_of", "files": {name: sha256}}
    <bucket>/<version>/<file>             the bundle `python -m export` writes
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from collections.abc import Callable
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO / "model"
BUNDLE = ("model.txt", "thresholds.json", "fighter_snapshot.parquet", "feature_spec.json", "manifest.json",
          "examples.json", "fighter_rating.parquet")
BAKED_VERSION = json.loads((MODEL_DIR / "manifest.json").read_text())["version"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish_version(bucket: Path, version: str, *, as_of: str = "2026-10-21", edit: Callable[[Path], None] | None = None,
                    corrupt: str | None = None, latest: bool = True) -> dict:
    """Upload a bundle as `version`: model/ with manifest.json's version and as_of changed. `edit(dir)` may change
    the files first (they are hashed after it, so the bundle is self-consistent); `corrupt` flips a byte in one
    file after hashing (a bad checksum). `latest=False` leaves latest.json alone. Returns latest.json's content."""
    out = bucket / version
    out.mkdir(parents=True)
    for name in BUNDLE:
        shutil.copy2(MODEL_DIR / name, out / name)
    manifest = json.loads((out / "manifest.json").read_text())
    manifest.update(version=version, as_of=as_of)
    (out / "manifest.json").write_text(json.dumps(manifest))
    if edit is not None:
        edit(out)
    doc = {"format_version": 1, "version": version, "as_of": as_of, "files": {n: sha256(out / n) for n in BUNDLE}}
    if corrupt is not None:
        data = bytearray((out / corrupt).read_bytes())
        data[len(data) // 2] ^= 0xFF
        (out / corrupt).write_bytes(bytes(data))
    if latest:
        write_latest(bucket, doc)
    return doc


def write_latest(bucket: Path, doc: dict) -> None:
    (bucket / "latest.json").write_text(json.dumps(doc))


class Bucket:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir()
        self.requests: list[str] = []
        self.user_agents: list[str] = []
        requests, user_agents = self.requests, self.user_agents

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002 - the stdlib's name
                requests.append(self.path)
                user_agents.append(self.headers.get("User-Agent", ""))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def publish(self, version: str, **kwargs) -> dict:
        return publish_version(self.root, version, **kwargs)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class Redirector:
    """A server that answers every request with a 302 to `location`: a bucket that redirects somewhere else."""

    def __init__(self, location: str):
        class Handler(SimpleHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - the stdlib's name
                self.send_response(302)
                self.send_header("Location", location)
                self.end_headers()

            def log_message(self, format, *args):  # noqa: A002
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
