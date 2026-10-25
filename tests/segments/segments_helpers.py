"""Offline ESPN fixtures and transport for the segments stage."""

from __future__ import annotations

import json
from pathlib import Path

from fightcard.images.http import Response

FIXTURES = Path(__file__).parent.parent / "fixtures" / "espn"


def fixture_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTransport:
    """Routes by URL prefix to canned responses; unknown URLs are 404s."""

    def __init__(self) -> None:
        self.routes: dict[str, object] = {}
        self.calls: list[str] = []

    def add_json(self, prefix: str, data: dict) -> None:
        self.routes[prefix] = Response(200, json.dumps(data).encode())

    def add(self, prefix: str, response) -> None:
        self.routes[prefix] = response

    def __call__(self, url: str, params: dict | None = None):
        self.calls.append(url)
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response
        return Response(404, b"")
