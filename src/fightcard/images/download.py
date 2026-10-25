"""Validate downloaded bytes are a real headshot and normalise them to small WebP."""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

MIN_SIDE = 64  # ESPN/Wikipedia placeholders and tracking pixels are smaller
_EXT = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
MAX_WIDTH = 320  # site renders headshots well below this
WEBP_QUALITY = 82


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


def normalize_image(data: bytes) -> bytes | None:
    """WebP bytes, at most MAX_WIDTH wide (never upscaled), metadata stripped; None if invalid."""
    if validate_image(data) is None:
        return None
    with Image.open(io.BytesIO(data)) as img:
        has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        out = img.convert("RGBA" if has_alpha else "RGB")
    if out.width > MAX_WIDTH:
        out = out.resize((MAX_WIDTH, round(out.height * MAX_WIDTH / out.width)), Image.LANCZOS)
    out.info = {}  # drop EXIF/ICC/XMP carried over from the source
    buf = io.BytesIO()
    out.save(buf, format="WEBP", quality=WEBP_QUALITY, method=6)
    return buf.getvalue()
