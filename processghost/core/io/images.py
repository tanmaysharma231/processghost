"""Image encoding helpers for sending extracted frames to a vision model."""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _resize_to_max_dimension(img: Image.Image, max_dimension: int) -> Image.Image:
    """Downscale img so its longer side is at most max_dimension, preserving aspect ratio.

    Vision-model token cost for an image scales with its pixel dimensions (it's tiled into
    fixed-size blocks internally), not its file size -- a full 1920x1080 screen-recording frame
    costs far more per call than the pipeline needs for recognizing UI elements and on-screen
    text. If the image is already at or under max_dimension, it is returned unchanged (never
    upscaled).
    """
    width, height = img.size
    longer_side = max(width, height)
    if max_dimension <= 0 or longer_side <= max_dimension:
        return img
    scale = max_dimension / longer_side
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return img.resize(new_size, Image.LANCZOS)


def frame_to_data_uri(frame_path: Path, max_dimension: int | None = None) -> str:
    """Read an extracted frame from disk and return it as a base64 data: URI.

    If max_dimension is given, the frame is downscaled (preserving aspect ratio, never upscaled)
    before encoding -- see core.config.VISION_MAX_DIMENSION for the pipeline's default. Kept as
    PNG throughout (not re-compressed to JPEG) since the cost lever here is pixel dimensions, not
    file size, and PNG preserves the on-screen text legibility the vision call depends on.

    Used to attach a frame image to an OpenAI vision chat message via
    ``{"type": "image_url", "image_url": {"url": <data uri>}}``.
    """
    if max_dimension is None:
        mime = _MIME_BY_SUFFIX.get(frame_path.suffix.lower(), "image/png")
        encoded = base64.b64encode(frame_path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    with Image.open(frame_path) as img:
        img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
        resized = _resize_to_max_dimension(img, max_dimension)
        buf = io.BytesIO()
        resized.save(buf, format="PNG")
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def frame_to_image_block(frame_path: Path, max_dimension: int | None = None) -> dict:
    """A provider-neutral base64 image content block, as LangChain's chat models accept it:
    {"type": "image", "base64": ..., "mime_type": ...}. Same resize rules as frame_to_data_uri."""
    header, _, encoded = frame_to_data_uri(frame_path, max_dimension).partition(",")
    mime = header[len("data:"):].split(";")[0]
    return {"type": "image", "base64": encoded, "mime_type": mime}
