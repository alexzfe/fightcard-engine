"""`fightcard publish --data <data> --out <root>`: validate data/ and publish it as the /v1/ files.

The new generation goes live atomically (fightcard.publish.generations). Invalid data or a failed write exits
1 and the previous generation stays live.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fightcard.publish.build import DataError, build
from fightcard.publish.generations import KEEP, publish


def _positive(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def add_arguments(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS keeps a top-level `fightcard --data X publish` from being overwritten by a default here.
    parser.add_argument("--data", type=Path, default=argparse.SUPPRESS, help="data/ directory to publish")
    parser.add_argument("--out", type=Path, required=True, help="publish root; nginx serves <out>/current")
    parser.add_argument("--keep", type=_positive, default=KEEP, help=f"generations to keep (default {KEEP})")


def run(args: argparse.Namespace) -> int:
    try:
        files = build(Path(args.data))
    except DataError as e:
        print(f"publish: {e}", file=sys.stderr)
        return 1
    try:
        gen = publish(files, Path(args.out), keep=args.keep)
    except OSError as e:
        print(f"publish: cannot write {args.out}: {e}", file=sys.stderr)
        return 1
    if gen.changed:
        print(f"publish: generation {gen.id} is live ({len(files)} files)")
    else:
        print(f"publish: unchanged, generation {gen.id} stays live")
    return 0
