"""Plain helpers for images tests (unique module name: avoids clashing with other test dirs)."""

from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load_fixture(rel: str) -> dict:
    return json.loads((FIXTURES / rel).read_text())


def png_bytes(width: int = 300, height: int = 220, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 30, 30)).save(buf, format=fmt)
    return buf.getvalue()
