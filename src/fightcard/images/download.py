"""Validate downloaded bytes are a real, non-placeholder image."""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

MIN_SIDE = 64  # ESPN/Wikipedia placeholders and tracking pixels are smaller
_EXT = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}


def validate_image(data: bytes) -> str | None:
    """File extension for a usable headshot, or None if it's not one."""
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
            fmt, (w, h) = img.format, img.size
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return None
    if fmt not in _EXT or min(w, h) < MIN_SIDE:
        return None
    return _EXT[fmt]
