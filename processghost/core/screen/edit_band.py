"""The name-box / formula-bar band: where a spreadsheet shows which cell is selected and what it holds (docs/26 flow plan).

A text edit in one cell moves very few pixels: a few characters typed, or a dropdown value swapped, can fall under the
whole-frame threshold (config.SCREEN_ACTIVE_FRAC) and never become an event. The cell's text is always echoed in the
formula bar, though, so that one row is worth watching on its own.

Two steps, no model:
1. FIND the band once from an early full frame: the row that holds the formula-bar glyph ("fx"), taken full width with a
   little padding. No glyph found (not a spreadsheet, or not legible) means no band, and nothing here changes.
2. WATCH it: a sample counts as changed when the band's text differs from the previous sample's. Pixels alone cannot say
   (a moving mouse pointer or a blinking caret changes a few pixels and no text), so a small pixel change only decides
   whether the band is read; the reading decides whether it changed.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from core import config
from core.screen.diff import normalize_frame
from core.screen.ocr import OcrFn, OcrLinesFn
from core.text_grounding import compact

Band = Tuple[int, int]  # (y0, y1) in original pixels, full width

_GLYPH = re.compile(r"^[Il1|]?fx", re.I)
_GLYPH_MAX_CHARS = 6  # "fx" plus up to a few debris characters from the cancel/accept icons beside it
_GLYPH_MAX_X_SHARE = 0.25  # the glyph sits at the left, right after the name box


def find_edit_band(frames: Sequence[np.ndarray], lines: OcrLinesFn) -> Optional[Band]:
    """The band of the first of `frames` in which the formula-bar glyph is found, or None."""
    for frame in frames:
        h, w = frame.shape[:2]
        top = frame[: int(h * config.SCREEN_BAND_SEARCH_PCT)]
        for text, (x0, y0, _x1, y1) in sorted(lines(top), key=lambda item: item[1][1]):
            stripped = text.strip()
            if _GLYPH.match(stripped) and len(stripped) <= _GLYPH_MAX_CHARS and x0 < w * _GLYPH_MAX_X_SHARE:
                return max(0, y0 - config.SCREEN_BAND_PAD_PX), min(h, y1 + config.SCREEN_BAND_PAD_PX)
    return None


def band_changed_frac(prev: np.ndarray, curr: np.ndarray, band: Band) -> float:
    """Share of the band's pixels that differ between two frames, in the same normalised space as the whole-frame diff."""
    y0, y1 = band
    a, _ = normalize_frame(prev[y0:y1])
    b, _ = normalize_frame(curr[y0:y1])
    changed = cv2.absdiff(a, b) > config.SCREEN_PIXEL_THRESH
    return float(np.count_nonzero(changed)) / changed.size


def text_changed(before: str, after: str) -> bool:
    """True when the band's two readings are different text, not OCR jitter (same ratio rule the change records use)."""
    a, b = compact(before), compact(after)
    if a == b:
        return False
    return SequenceMatcher(None, a, b).ratio() < config.SCREEN_TEXT_CHANGE_MIN_RATIO


class BandWatch:
    """Reads the band of samples lazily and answers "did its text change since the previous sample?"."""

    def __init__(self, frames: List[np.ndarray], band: Band, ocr: OcrFn):
        self._frames, self._band, self._ocr = frames, band, ocr
        self._text: Dict[int, str] = {}
        self.reads = 0

    def _read(self, i: int) -> Optional[str]:
        if i not in self._text:
            if self.reads >= config.SCREEN_BAND_MAX_OCR:
                return None
            y0, y1 = self._band
            self._text[i] = self._ocr(self._frames[i][y0:y1])
            self.reads += 1
        return self._text[i]

    def changed(self, i: int) -> bool:
        """Sample `i` against sample `i - 1`. Idle samples cost no OCR: only a visible pixel change in the band is read."""
        if band_changed_frac(self._frames[i - 1], self._frames[i], self._band) < config.SCREEN_BAND_PREFILTER_FRAC:
            return False
        before, after = self._read(i - 1), self._read(i)
        return before is not None and after is not None and text_changed(before, after)
