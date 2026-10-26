"""`fightcard model pull`: adopt the model research publishes, when it passes every gate (docs/SERVICE.md).

    --url      the bucket (default $FIGHTCARD_MODEL_URL; unset and no pin: nothing happens)
    --pin      a kept version to run, whatever the bucket says (default $FIGHTCARD_MODEL_PIN)
    --root     the model state dir (default <--data>/../model, beside data/)
    --seed-dir the image's baked model, copied in on the first run (default $FIGHTCARD_MODEL_DIR, else model/)

A model that fails a gate, or a bucket that can't be reached, is logged and keeps the current model: the exit
code is 0, so the service run goes on and scores with what it has."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from fightcard.model.pull import KEEP, pull


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("pull", help="adopt the newest published model if it passes every gate")
    p.add_argument("--url", default=os.environ.get("FIGHTCARD_MODEL_URL") or None,
                   help="model bucket (default $FIGHTCARD_MODEL_URL)")
    p.add_argument("--pin", default=os.environ.get("FIGHTCARD_MODEL_PIN") or None,
                   help="run this kept version and pull nothing (default $FIGHTCARD_MODEL_PIN)")
    p.add_argument("--root", type=Path, default=None, help="model state dir (default <--data>/../model)")
    p.add_argument("--seed-dir", type=Path, default=Path(os.environ.get("FIGHTCARD_MODEL_DIR", "model")),
                   help="the baked model that seeds the first run (default $FIGHTCARD_MODEL_DIR, else model/)")
    p.add_argument("--keep", type=int, default=KEEP, help=f"versions to keep (default {KEEP})")


def run(args: argparse.Namespace) -> int:
    root = args.root or args.data.resolve().parent / "model"
    pull(root, url=args.url, pin=args.pin, seed_dir=args.seed_dir, keep=args.keep,
         log=lambda line: print(f"model pull: {line}", file=sys.stderr))
    return 0
