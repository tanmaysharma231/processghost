"""Pixel-diff primitive for the screen timeline (docs/22 stage 0b): where did the screen change
between two frames?

Plain OpenCV, no model calls. Screens are static between discrete jumps, so a thresholded absolute
difference followed by connected components finds a single cell changing as readily as a whole
window swap -- unlike a mean-frame-difference score, which a small local edit barely moves.

Used by scripts/checker_replay.py (to show which of the frames a past Checker call saw actually
differed) and, next, by the screen timeline itself.
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional, Sequence, Tuple

import cv2
import numpy as np

from core import config

# (x, y, w, h) in ORIGINAL frame pixels.
Box = Tuple[int, int, int, int]


class DiffResult(NamedTuple):
    boxes: List[Box]  # merged changed regions, in the original frames' pixel coordinates
    changed_frac: float  # fraction of the (downscaled) frame whose pixels changed, after masking
    identical: bool  # changed_frac < config.SCREEN_NO_CHANGE_FRAC and no box survived


def normalize_frame(img: np.ndarray) -> Tuple[np.ndarray, float]:
    """Downscale to config.SCREEN_DIFF_WIDTH, grayscale, blur. Returns (gray, scale) where scale
    is downscaled_width / original_width, so a box can be mapped back to original pixels."""
    h, w = img.shape[:2]
    scale = min(1.0, config.SCREEN_DIFF_WIDTH / float(w))
    if scale < 1.0:
        img = cv2.resize(img, (int(round(w * scale)), int(round(h * scale))), interpolation=cv2.INTER_AREA)
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    k = config.SCREEN_BLUR_KSIZE
    return cv2.GaussianBlur(gray, (k, k), 0), scale


def diff_boxes(
    prev: np.ndarray,
    curr: np.ndarray,
    mask_rects: Optional[Sequence[Box]] = None,
) -> DiffResult:
    """Boxes around the regions that changed between `prev` and `curr` (same-size BGR or gray
    arrays). `mask_rects` are (x, y, w, h) in original pixels that never count as change (meeting
    thumbnails, clock, banners)."""
    if prev.shape[:2] != curr.shape[:2]:
        raise ValueError(f"frame size mismatch: {prev.shape[:2]} vs {curr.shape[:2]}")

    a, scale = normalize_frame(prev)
    b, _ = normalize_frame(curr)
    delta = cv2.absdiff(a, b)
    changed = (delta > config.SCREEN_PIXEL_THRESH).astype(np.uint8) * 255

    for x, y, w, h in mask_rects or ():
        x0, y0 = int(x * scale), int(y * scale)
        changed[y0 : y0 + int(np.ceil(h * scale)), x0 : x0 + int(np.ceil(w * scale))] = 0

    changed_frac = float(np.count_nonzero(changed)) / changed.size

    k = config.SCREEN_DILATE_PX
    merged = cv2.dilate(changed, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, _labels, stats, _centroids = cv2.connectedComponentsWithStats(merged, connectivity=8)

    boxes: List[Box] = []
    for i in range(1, n):  # label 0 is the background
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < config.SCREEN_MIN_BOX_AREA:
            continue
        boxes.append((int(x / scale), int(y / scale), int(np.ceil(w / scale)), int(np.ceil(h / scale))))

    boxes.sort(key=lambda bx: (bx[1], bx[0]))
    return DiffResult(boxes=boxes, changed_frac=changed_frac, identical=not boxes and changed_frac < config.SCREEN_NO_CHANGE_FRAC)
