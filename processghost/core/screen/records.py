"""Machine-extracted change records (docs/24): for each screen event, the OCR text of the frame's
chrome strips before and after, and of each changed region before and after. No model call; OCR is
local, and cached by image content so a rerun costs nothing.

The records are what the Reconciler reads instead of pictures, and what it must quote from: a claim
about what changed that is not a substring of a record is rejected in core/screen/reconcile.py.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

from core import config
from core.llm import cache
from core.models.screen import ChangeRecord, RegionChange, ScreenEvent, ScreenTimeline
from core.screen.evidence import _padded_crop
from core.screen.ocr import OcrFn
from core.screen.timeline import chrome_text


def _cached(ocr: OcrFn) -> OcrFn:
    """Wrap `ocr` with the content-addressed cache, keyed on the pixels it is given."""

    def run(img: np.ndarray) -> str:
        key = cache.make_key("ocr", config.SCREEN_RECORD_CROP_PAD_PX, list(img.shape), hashlib.sha256(img.tobytes()).hexdigest())
        hit = cache.get("ocr", key)
        if hit is not None:
            return hit["text"]
        text = ocr(img)
        cache.put("ocr", key, {"text": text})
        return text

    return run


def _cap(text: str) -> str:
    return text if len(text) <= config.SCREEN_RECORD_MAX_CHARS else text[: config.SCREEN_RECORD_MAX_CHARS]


def record_for_event(ev: ScreenEvent, timeline_dir: Path, ocr: OcrFn) -> Optional[ChangeRecord]:
    before = cv2.imread(str(timeline_dir / ev.before_frame))
    after = cv2.imread(str(timeline_dir / ev.after_frame))
    if before is None or after is None:
        return None
    boxes = sorted(ev.bboxes, key=lambda b: b[2] * b[3], reverse=True)[: config.SCREEN_RECORD_MAX_REGIONS]
    pad = config.SCREEN_RECORD_CROP_PAD_PX
    return ChangeRecord(
        event_id=ev.event_id,
        t_start=ev.t_start,
        t_end=ev.t_end,
        type=ev.type,
        chrome_before=_cap(chrome_text(before, ocr)),
        chrome_after=_cap(chrome_text(after, ocr)),
        regions=[
            RegionChange(box=b, before=_cap(ocr(_padded_crop(before, b, pad))), after=_cap(ocr(_padded_crop(after, b, pad))))
            for b in boxes
        ],
    )


def build_change_records(timeline: ScreenTimeline, timeline_dir: Path, ocr: OcrFn) -> List[ChangeRecord]:
    cached = _cached(ocr)
    records = (record_for_event(ev, timeline_dir, cached) for ev in timeline.events)
    return [r for r in records if r is not None]
