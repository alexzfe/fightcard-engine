"""Fixtures for the segments stage (helpers live in segments_helpers.py)."""

from __future__ import annotations

import pytest
from segments_helpers import FakeTransport

from fightcard.images.http import HttpClient


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def client(transport) -> HttpClient:
    return HttpClient(transport=transport, min_interval=0, sleep=lambda _s: None)
