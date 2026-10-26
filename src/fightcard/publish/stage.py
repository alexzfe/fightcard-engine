"""`fightcard publish --data <data> --out <root>`: validate data/ and publish it as the /v1/ files.

Invalid data exits 1 and publishes nothing, so the live files stay as they were.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from fightcard.publish.build import DataError, build


def _write(files: dict[str, bytes], out: Path) -> None:
    current = out / "current"
    shutil.rmtree(current, ignore_errors=True)
    for name, blob in files.items():
        path = current / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS keeps a top-level `fightcard --data X publish` from being overwritten by a default here.
    parser.add_argument("--data", type=Path, default=argparse.SUPPRESS, help="data/ directory to publish")
    parser.add_argument("--out", type=Path, required=True, help="publish root; nginx serves <out>/current")


def run(args: argparse.Namespace) -> int:
    try:
        files = build(Path(args.data))
    except DataError as e:
        print(f"publish: {e}", file=sys.stderr)
        return 1
    _write(files, Path(args.out))
    print(f"publish: {len(files)} files")
    return 0
