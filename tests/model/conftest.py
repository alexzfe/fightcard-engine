from __future__ import annotations

import pytest
from model_bucket import Bucket


@pytest.fixture
def bucket(tmp_path):
    b = Bucket(tmp_path / "bucket")
    yield b
    b.stop()
