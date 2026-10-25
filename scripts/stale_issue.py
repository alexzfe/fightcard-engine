"""Build the body of the "Re-export finish model" issue from data/stale_fighters.json.

`fightcard score` writes that file when a fighter's ufcstats record no longer matches the model's snapshot
(they've fought since the export), and removes it when nothing is stale. refresh.yml opens, updates or
closes the issue with `gh`; this only writes the body and says whether there is anything to report.

    python3 scripts/stale_issue.py --data data --out stale-issue.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

TITLE = "Re-export finish model"
# Lets the workflow (and a human) recognise the bot's issue regardless of edits to the title.
MARKER = "<!-- fightcard:stale-model -->"
FIGHTER_URL = "http://ufcstats.com/fighter-details/{}"


def load_stale(data_dir: Path) -> list[dict]:
    path = data_dir / "stale_fighters.json"
    if not path.exists():
        return []
    entries = json.loads(path.read_text())
    if not isinstance(entries, list):
        raise ValueError(f"{path}: expected a list")
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("fighter_id"):
            raise ValueError(f"{path}: entry without fighter_id: {entry!r}")
    return entries


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|")


def build_body(stale: list[dict], *, events: dict[str, str], model_as_of: str | None) -> str:
    rows = {(e["fighter_id"], e.get("event_id", "")): e for e in stale}
    ordered = sorted(rows.values(), key=lambda e: (e.get("name", ""), e.get("event_id", "")))
    fighters = len({e["fighter_id"] for e in ordered})
    since = f" (as of {model_as_of})" if model_as_of else ""

    lines = [
        MARKER,
        f"{fighters} fighter{'s' if fighters != 1 else ''} have fought since the finish model was exported{since}. "
        "Their upcoming bouts show `—` until the model is re-exported.",
        "",
        "| Fighter | Event |",
        "|---|---|",
    ]
    for e in ordered:
        name = _cell(e.get("name") or e["fighter_id"])
        event_id = e.get("event_id", "")
        lines.append(f"| [{name}]({FIGHTER_URL.format(e['fighter_id'])}) | {_cell(events.get(event_id, event_id))} |")
    lines += [
        "",
        "To re-export:",
        "",
        "```sh",
        "cd ~/Projects/fight-auto-research && uv run python -m export --out /tmp/finish-model",
        "cp /tmp/finish-model/* ~/Projects/fight-card-website/model/",
        "```",
        "",
        "Commit `model/` and push; the next refresh rescores the bouts and closes this issue.",
        "",
    ]
    return "\n".join(lines)


def _event_names(data_dir: Path, event_ids: set[str]) -> dict[str, str]:
    names = {}
    for event_id in event_ids:
        path = data_dir / "events" / f"{event_id}.json"
        if path.exists():
            names[event_id] = json.loads(path.read_text()).get("name", event_id)
    return names


def _model_as_of(data_dir: Path) -> str | None:
    path = data_dir / "index.json"
    if not path.exists():
        return None
    return (json.loads(path.read_text()).get("model") or {}).get("as_of")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, required=True, help="where to write the issue body")
    args = parser.parse_args(argv)

    stale = load_stale(args.data)
    if stale:
        events = _event_names(args.data, {e.get("event_id", "") for e in stale})
        args.out.write_text(build_body(stale, events=events, model_as_of=_model_as_of(args.data)))

    lines = [f"stale={'true' if stale else 'false'}", f"count={len({e['fighter_id'] for e in stale})}"]
    print(" ".join(lines))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as f:
            f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
