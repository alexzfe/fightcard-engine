from pathlib import Path


def add_arguments(parser):
    parser.add_argument("--model-dir", type=Path, default=Path("model"))
    parser.add_argument("--now", default=None)


def run(args):
    raise NotImplementedError
