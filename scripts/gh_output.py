"""Shared by the workflow scripts: print `key=value` lines and append them to $GITHUB_OUTPUT when set."""

from __future__ import annotations

import os


def emit(**outputs: object) -> None:
    lines = [f"{key}={str(value).lower() if isinstance(value, bool) else value}" for key, value in outputs.items()]
    print(" ".join(lines))
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a") as f:
            f.write("\n".join(lines) + "\n")
