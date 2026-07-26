"""Unit tests for core/io/images.py -- the vision-call cost guard (downscale before encoding)."""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from core.io.images import _resize_to_max_dimension, frame_to_data_uri


def _make_frame(path, size):
    Image.new("RGB", size, color=(200, 50, 100)).save(path, format="PNG")
    return path


def _decoded_size(data_uri: str) -> tuple[int, int]:
    header, b64data = data_uri.split(",", 1)
    raw = base64.b64decode(b64data)
    with Image.open(io.BytesIO(raw)) as img:
        return img.size


# --- _resize_to_max_dimension -----------------------------------------------

def test_resize_downscales_landscape_preserving_aspect_ratio():
    img = Image.new("RGB", (1920, 1080))
    resized = _resize_to_max_dimension(img, 1024)
    assert max(resized.size) == 1024
    assert resized.size[0] / resized.size[1] == pytest.approx(1920 / 1080, rel=1e-2)


def test_resize_downscales_portrait_preserving_aspect_ratio():
    img = Image.new("RGB", (1080, 1920))
    resized = _resize_to_max_dimension(img, 1024)
    assert max(resized.size) == 1024
    assert resized.size[1] / resized.size[0] == pytest.approx(1920 / 1080, rel=1e-2)


def test_resize_leaves_small_image_unchanged():
    img = Image.new("RGB", (400, 300))
    resized = _resize_to_max_dimension(img, 1024)
    assert resized.size == (400, 300)


def test_resize_leaves_image_unchanged_at_exact_max():
    img = Image.new("RGB", (1024, 768))
    resized = _resize_to_max_dimension(img, 1024)
    assert resized.size == (1024, 768)


def test_resize_never_upscales():
    img = Image.new("RGB", (200, 100))
    resized = _resize_to_max_dimension(img, 4000)
    assert resized.size == (200, 100)


def test_resize_non_positive_max_dimension_returns_unchanged():
    img = Image.new("RGB", (1920, 1080))
    assert _resize_to_max_dimension(img, 0).size == (1920, 1080)
    assert _resize_to_max_dimension(img, -1).size == (1920, 1080)


# --- frame_to_data_uri -------------------------------------------------------

def test_frame_to_data_uri_without_max_dimension_is_unchanged(tmp_path):
    frame = _make_frame(tmp_path / "frame.png", (1920, 1080))
    uri = frame_to_data_uri(frame)
    assert uri.startswith("data:image/png;base64,")
    assert _decoded_size(uri) == (1920, 1080)


def test_frame_to_data_uri_with_max_dimension_downscales(tmp_path):
    frame = _make_frame(tmp_path / "frame.png", (1920, 1080))
    uri = frame_to_data_uri(frame, max_dimension=1024)
    assert max(_decoded_size(uri)) == 1024


def test_frame_to_data_uri_with_max_dimension_smaller_image_unchanged(tmp_path):
    frame = _make_frame(tmp_path / "frame.png", (640, 480))
    uri = frame_to_data_uri(frame, max_dimension=1024)
    assert _decoded_size(uri) == (640, 480)


def test_frame_to_data_uri_downscaled_payload_is_smaller(tmp_path):
    frame = _make_frame(tmp_path / "frame.png", (1920, 1080))
    full = frame_to_data_uri(frame)
    downscaled = frame_to_data_uri(frame, max_dimension=1024)
    assert len(downscaled) < len(full)
