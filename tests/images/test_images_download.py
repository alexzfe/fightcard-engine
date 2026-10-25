from images_helpers import png_bytes

from fightcard.images.download import validate_image


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
