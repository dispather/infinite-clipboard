"""Regression: clipboard image -> PNG encoding across Pillow 13 (2026-11-24).

Pillow 13 removed saving mode "I" images as PNG (12.x clipped to 16 bits with a
DeprecationWarning). `_image_to_png_bytes` must keep encoding such images instead of
raising OSError, which would silently drop that clipboard copy.
"""
import io

from PIL import Image

from core.clipboard_manager import _image_to_png_bytes


def _roundtrip(image: Image.Image) -> Image.Image:
    data = _image_to_png_bytes(image)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return Image.open(io.BytesIO(data))


def test_mode_i_32bit_tiff_encodes_as_16bit_png():
    # 32-bit int TIFF (macOS pasteboard TIFF path opens it as mode "I")
    src = io.BytesIO()
    Image.new("I", (4, 4), 70000).save(src, "TIFF")
    image = Image.open(io.BytesIO(src.getvalue()))
    assert image.mode == "I"

    out = _roundtrip(image)
    assert out.size == (4, 4)
    # Same result as Pillow 12.x: values clipped to the 16-bit max
    assert out.getpixel((0, 0)) == 65535


def test_common_modes_unchanged():
    for mode, color in [("RGBA", (1, 2, 3, 4)), ("RGB", (1, 2, 3)), ("L", 7), ("I;16", 40000)]:
        out = _roundtrip(Image.new(mode, (2, 2), color))
        assert out.mode == mode
        assert out.getpixel((0, 0)) == color
