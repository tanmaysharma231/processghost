"""Reading fallback (docs/26 flow plan): when a field that should be readable is not, read it again in a
different way before giving up, and ask a cheap vision model only as the last step.

A field is "failed" when the page is a spreadsheet and its selected-cell reference did not validate. The
ladder per failed field:

    1. re-read the same frame with a CHANGED input (2x upscale, then local contrast, then a taller strip).
       Re-running OCR on identical pixels gives the identical wrong answer, so each retry differs.
    2. queue the still-failing top strip; ONE batched call to the cheap vision model transcribes all of them.
       Its answer must pass the same validator as OCR output, is tagged read_by="vlm", and is capped per run.
       Over the cap, or an answer that fails validation, the field stays unread: never guessed.

Observation only. A model reading is evidence only after it validates.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
from pydantic import BaseModel, Field

from core import config
from core.llm import prompts
from core.models.screen import ChangeRecord, Locator, ScreenEvent, ScreenTimeline
from core.screen.ocr import OcrFn
from core.screen.surfaces import diff_locators, locate
from core.screen.timeline import chrome_text

_CELL_REF = re.compile(r"^[A-Z]{1,3}[0-9]{1,7}$")
_TALL_STRIP_PCT = 0.20  # the third retry reads a taller top strip than the default chrome strip


class VlmReading(BaseModel):
    id: str
    cell_ref: str = ""
    content: str = ""


class VlmReadOutput(BaseModel):
    readings: List[VlmReading] = Field(default_factory=list)


def valid_cell_ref(text: str) -> bool:
    return bool(_CELL_REF.match((text or "").strip().upper()))


def _upscale(frame: np.ndarray) -> np.ndarray:
    return cv2.resize(frame, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)


def _contrast(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray), cv2.COLOR_GRAY2BGR)


def top_strip(frame: np.ndarray) -> np.ndarray:
    return frame[: int(frame.shape[0] * _TALL_STRIP_PCT)]


def _read_variants() -> List[Tuple[str, Callable[[np.ndarray, OcrFn], str]]]:
    """(name, read) pairs, in order. Each read returns chrome-like text for `locate`."""
    return [
        ("upscale", lambda f, ocr: chrome_text(_upscale(f), ocr)),
        ("contrast", lambda f, ocr: chrome_text(_contrast(f), ocr)),
        ("tall_strip", lambda f, ocr: ocr(_upscale(top_strip(f)))),
    ][: config.OCR_RETRY_VARIANTS]


def _needs_repair(loc: Optional[Locator]) -> bool:
    return loc is not None and loc.surface == "spreadsheet" and not valid_cell_ref(loc.focus)


def _with_reading(loc: Locator, *, focus: str, content: str, by: str) -> Locator:
    read_by = dict(loc.read_by)
    read_by["focus"] = by
    update = {"focus": focus.strip().upper(), "read_by": read_by}
    if content and not loc.focus_content:
        update["focus_content"] = content.strip()
        read_by["focus_content"] = by
    return loc.model_copy(update=update)


def retry_locator(frame: np.ndarray, loc: Locator, ocr: OcrFn) -> Locator:
    """Try the changed-input re-reads in order; return the repaired locator or the original unchanged."""
    for _name, read in _read_variants():
        again = locate(read(frame, ocr))
        if again.surface == "spreadsheet" and valid_cell_ref(again.focus):
            return _with_reading(loc, focus=again.focus, content=again.focus_content, by="ocr_retry")
    return loc


def _frame_path(ev: ScreenEvent, side: str) -> str:
    return ev.before_frame if side == "before" else ev.after_frame


def repair_records(
    records: List[ChangeRecord],
    timeline: ScreenTimeline,
    timeline_dir: Path,
    ocr: OcrFn,
    client,
    work_dir: Path,
) -> Tuple[List[ChangeRecord], Dict[str, int]]:
    """Return (records with unread selected-cell fields repaired where possible, counts). Non-destructive:
    a field that cannot be repaired is left exactly as it was. `client=None` makes no model call (dry run)."""
    events = {e.event_id: e for e in timeline.events}
    by_event = {r.event_id: r for r in records}
    stats = {"failed": 0, "ocr_retry_fixed": 0, "vlm_queued": 0, "vlm_fixed": 0, "vlm_rejected": 0, "over_cap": 0}
    locs: Dict[Tuple[str, str], Locator] = {}
    queue: List[Tuple[str, str, Path]] = []  # (event_id, side, strip image path)

    for rec in records:
        ev = events.get(rec.event_id)
        if ev is None:
            continue
        for side, loc in (("before", rec.surface_before), ("after", rec.surface_after)):
            if not _needs_repair(loc):
                continue
            stats["failed"] += 1
            frame = cv2.imread(str(timeline_dir / _frame_path(ev, side)))
            if frame is None:
                continue
            fixed = retry_locator(frame, loc, ocr)
            if fixed is not loc:
                stats["ocr_retry_fixed"] += 1
                locs[(rec.event_id, side)] = fixed
                continue
            if len(queue) >= config.VLM_READ_MAX_CROPS:
                stats["over_cap"] += 1
                continue
            work_dir.mkdir(parents=True, exist_ok=True)
            path = work_dir / ("read_%s_%s.png" % (rec.event_id, side))
            cv2.imwrite(str(path), top_strip(frame))
            queue.append((rec.event_id, side, path))

    stats["vlm_queued"] = len(queue)
    if queue and client is not None:  # client=None is a dry run: crops are written but nothing is sent
        ids = ["%s/%s" % (e, s) for e, s, _ in queue]
        user = "Images, in order: " + ", ".join("image %d has id %s" % (i + 1, k) for i, k in enumerate(ids))
        out = client.structured_chat(
            prompts.READ_SYSTEM, user, VlmReadOutput, stage="read_fallback",
            images=[p for _, _, p in queue],
        )
        by_id = {r.id: r for r in out.readings}
        for (event_id, side, _path), key in zip(queue, ids):
            reading = by_id.get(key)
            rec = by_event[event_id]
            loc = rec.surface_before if side == "before" else rec.surface_after
            if reading is not None and valid_cell_ref(reading.cell_ref):
                locs[(event_id, side)] = _with_reading(loc, focus=reading.cell_ref, content=reading.content, by="vlm")
                stats["vlm_fixed"] += 1
            else:
                stats["vlm_rejected"] += 1

    repaired = []
    for rec in records:
        before = locs.get((rec.event_id, "before"), rec.surface_before)
        after = locs.get((rec.event_id, "after"), rec.surface_after)
        if before is rec.surface_before and after is rec.surface_after:
            repaired.append(rec)
            continue
        repaired.append(
            rec.model_copy(update={"surface_before": before, "surface_after": after, "surface_change": diff_locators(before, after)})
        )
    return repaired, stats
