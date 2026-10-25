"""Shared fakes for the images stage: an offline HTTP transport and a fake clock."""

from __future__ import annotations

import json

import pytest


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeTransport:
    """Routes requests by (url-prefix, query substring) to canned responses."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, object]] = []
        self.calls: list[tuple[str, dict]] = []

    def add(self, prefix: str, response, query: str = "") -> None:
        self.routes.append((prefix, query, response))

    def add_json(self, prefix: str, data: dict, query: str = "") -> None:
        from fightcard.images.http import Response

        self.add(prefix, Response(200, json.dumps(data).encode()), query)

    def __call__(self, url: str, params: dict | None = None):
        from fightcard.images.http import Response

        params = params or {}
        self.calls.append((url, params))
        flat = " ".join(str(v) for v in params.values())
        for prefix, query, response in self.routes:
            if url.startswith(prefix) and query in flat:
                if isinstance(response, Exception):
                    raise response
                return response
        return Response(404, b"")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()
