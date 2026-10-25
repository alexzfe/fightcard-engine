from images_helpers import png_bytes

from fightcard.images.download import normalize_image, validate_image


def test_valid_png_returns_extension():
    assert validate_image(png_bytes()) == "png"


def test_valid_jpeg_returns_jpg():
    assert validate_image(png_bytes(fmt="JPEG")) == "jpg"


def test_tiny_placeholder_is_rejected():
    assert validate_image(png_bytes(1, 1)) is None
    assert validate_image(png_bytes(40, 40)) is None


def test_non_image_is_rejected():
    assert validate_image(b"<html>not found</html>") is None
    assert validate_image(b"") is None


def test_unsupported_format_is_rejected():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (300, 300)).save(buf, format="BMP")
    assert validate_image(buf.getvalue()) is None


def _open(data):
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def test_normalize_downscales_to_320_wide_webp_keeping_aspect():
    img = _open(normalize_image(png_bytes(600, 436)))
    assert img.format == "WEBP" and img.size == (320, 233)


def test_normalize_never_upscales():
    assert _open(normalize_image(png_bytes(200, 300))).size == (200, 300)


def test_normalize_keeps_transparency():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (400, 300), (0, 0, 0, 0)).save(buf, format="PNG")
    assert _open(normalize_image(buf.getvalue())).mode == "RGBA"


def test_normalize_strips_metadata():
    import io

    from PIL import Image

    exif = Image.Exif()
    exif[0x010E] = "secret description"
    buf = io.BytesIO()
    Image.new("RGB", (400, 300)).save(buf, format="JPEG", exif=exif.tobytes())
    out = _open(normalize_image(buf.getvalue()))
    assert not out.info.get("exif") and not out.info.get("icc_profile")


def test_normalize_is_much_smaller_than_source_png():
    import io
    import os

    from PIL import Image

    src = Image.frombytes("RGB", (600, 436), os.urandom(600 * 436 * 3))
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    assert len(normalize_image(buf.getvalue())) < len(buf.getvalue()) / 4


def test_normalize_rejects_invalid_or_placeholder():
    assert normalize_image(b"nope") is None
    assert normalize_image(png_bytes(10, 10)) is None
