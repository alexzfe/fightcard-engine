"""Helpers shared by the scripts tests. Named uniquely, not `conftest`, because pipeline/tests has conftest.py modules
too and CI collects both suites in one run. Importing this also puts scripts/ on sys.path."""

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
