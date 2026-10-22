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
from core.screen.regions import plan_regions
from core.screen.surfaces import diff_locators, locate
from core.screen.timeline import chrome_text


def cached_ocr(ocr: OcrFn) -> OcrFn:
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
    h_img, w_img = after.shape[:2]
    groups = plan_regions(
        ev.region_boxes(), config.SCREEN_RECORD_MAX_REGIONS, w_img, h_img, config.SCREEN_REGION_MAX_CROP_FRAC
    )
    boxes = [g.box for g in groups]
    pad = config.SCREEN_RECORD_CROP_PAD_PX
    chrome_before, chrome_after = _cap(chrome_text(before, ocr)), _cap(chrome_text(after, ocr))
    surface_before, surface_after = locate(chrome_before), locate(chrome_after)
    return ChangeRecord(
        event_id=ev.event_id,
        t_start=ev.t_start,
        t_end=ev.t_end,
        type=ev.type,
        chrome_before=chrome_before,
        chrome_after=chrome_after,
        regions=[
            RegionChange(box=b, before=_cap(ocr(_padded_crop(before, b, pad))), after=_cap(ocr(_padded_crop(after, b, pad))))
            for b in boxes
        ],
        transient=ev.transient,
        surface_before=surface_before,
        surface_after=surface_after,
        surface_change=diff_locators(surface_before, surface_after),
    )


def build_change_records(timeline: ScreenTimeline, timeline_dir: Path, ocr: OcrFn) -> List[ChangeRecord]:
    cached = cached_ocr(ocr)
    records = (record_for_event(ev, timeline_dir, cached) for ev in timeline.events)
    return [r for r in records if r is not None]
