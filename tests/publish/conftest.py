"""Fixtures for the publish stage (helpers live in publish_helpers.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from publish_helpers import make_data


@pytest.fixture
def data_dir(tmp_path) -> Path:
    return make_data(tmp_path / "data")


@pytest.fixture
def out(tmp_path) -> Path:
    return tmp_path / "srv"
