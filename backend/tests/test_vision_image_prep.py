"""Unit tests for VisionAnalyzer image-payload preparation.

Covers the offline image-prep helpers (`_pil_to_base64`, `_pil_to_data_url`):
RGB conversion for JPEG, alpha-aware format selection, max-side downscaling, and
the no-mutation guarantee on the caller's image. These guard the payload-size
optimization that keeps vision input tokens bounded.
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from app.services.vision_analyzer import VisionAnalyzer


@pytest.fixture
def analyzer():
    return VisionAnalyzer()


def _decode(data_url: str):
    assert data_url.startswith("data:")
    header, b64 = data_url.split(",", 1)
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    img.load()
    return header, img


def test_jpeg_base64_converts_non_rgb_modes(analyzer):
    """JPEG cannot hold alpha/palette modes; encoder must convert to RGB."""
    rgba = Image.new("RGBA", (10, 10), (255, 0, 0, 128))
    b64 = analyzer._pil_to_base64(rgba, format="JPEG", quality=85)
    out = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert out.format == "JPEG" and out.mode == "RGB"


def test_data_url_opaque_image_uses_jpeg(analyzer):
    rgb = Image.new("RGB", (32, 32), (10, 20, 30))
    header, img = _decode(analyzer._pil_to_data_url(rgb))
    assert header == "data:image/jpeg;base64" and img.format == "JPEG"


def test_data_url_rgba_image_uses_png(analyzer):
    rgba = Image.new("RGBA", (32, 32), (10, 20, 30, 40))
    header, img = _decode(analyzer._pil_to_data_url(rgba))
    assert header == "data:image/png;base64" and img.format == "PNG"


def test_data_url_downscales_to_max_side(analyzer):
    max_side = analyzer.settings.vision_max_image_side
    assert max_side > 0
    big = Image.new("RGB", (max_side * 2, max_side), (0, 0, 0))
    _, img = _decode(analyzer._pil_to_data_url(big))
    assert max(img.size) <= max_side


def test_data_url_does_not_mutate_caller_image(analyzer):
    max_side = analyzer.settings.vision_max_image_side
    big = Image.new("RGB", (max_side * 2, max_side), (0, 0, 0))
    original_size = big.size
    analyzer._pil_to_data_url(big)
    assert big.size == original_size  # caller's image untouched (copy used)
