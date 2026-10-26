"""`fightcard <stage>` entry point. Each stage lives in its own package and
exposes `add_arguments(parser)` and `run(args) -> int`."""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

STAGES = {
    "scrape": "fightcard.ufcstats.stage",
    "images": "fightcard.images.stage",
    "score": "fightcard.score.stage",
    "segments": "fightcard.segments.stage",
    "publish": "fightcard.publish.stage",
    "history": "fightcard.history.stage",
    "model": "fightcard.model.stage",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fightcard")
    parser.add_argument("--data", type=Path, default=Path("data"), help="data/ directory")
    sub = parser.add_subparsers(dest="stage", required=True)
    for name, module_path in STAGES.items():
        stage_parser = sub.add_parser(name)
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError:
            stage_parser.set_defaults(_module=None)
            continue
        module.add_arguments(stage_parser)
        stage_parser.set_defaults(_module=module)

    args = parser.parse_args(argv)
    if args._module is None:
        print(f"stage {args.stage!r} not implemented yet", file=sys.stderr)
        return 2
    return args._module.run(args)


if __name__ == "__main__":
    sys.exit(main())
