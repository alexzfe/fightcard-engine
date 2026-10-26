"""Helpers shared by the ops tests. Named uniquely, not `conftest`, because the other suites under tests/ have
conftest.py modules too and they are collected in one run. Importing this also puts scripts/ on sys.path."""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
