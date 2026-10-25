"""A second look at a cell reading the workbook disagrees with (docs/26 flow plan).

The workbook is a hypothesis the screen must back, never the reading itself. When a cell the recording did NOT change reads
differently in the video and in the file, either could be wrong: OCR can garble characters, and a workbook can be a different
version. One batched call to the cheap vision model looks at ONLY the cropped name-box / formula-bar row of the settled frame
and must choose between the first reading, the workbook's value, and "neither / empty". Three guards keep it honest:

- it is shown both candidates in a fixed neutral order and told the pixels alone decide, so agreeing with the workbook is not
  the easy answer;
- a choice of the workbook's value is accepted only if the local OCR of an enlarged crop also finds (nearly) that text, so a
  model cannot talk its way into the workbook without matching pixels;
- over the crop cap, on an error or on an unusable answer the cell stays unconfirmed, and an unconfirmed disagreement on an
  unchanged cell is still held against the workbook (the conservative outcome).
"""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import cv2
import numpy as np
from pydantic import BaseModel, Field

from core import config
from core.data.match import Confirm, Disputed
from core.llm import prompts
from core.models.screen import ScreenTimeline
from core.screen.ocr import OcrFn
from core.screen.read_fallback import top_strip
from core.text_grounding import compact

_FUZZY = 0.8  # OCR of the crop must be this similar to the workbook's value for a "workbook" choice to stand


class Choice(BaseModel):
    id: str
    choice: Literal["read", "workbook", "neither"] = "neither"


class VerifyOutput(BaseModel):
    answers: List[Choice] = Field(default_factory=list)


def _crop(frame: np.ndarray, band: Optional[Tuple[int, int]]) -> np.ndarray:
    return frame[band[0] : band[1]] if band else top_strip(frame)


def _supports(ocr: Optional[OcrFn], crop: np.ndarray, value: str) -> bool:
    """True if the local OCR of an enlarged crop shows (nearly) `value`. With no OCR available nothing can corroborate."""
    if ocr is None:
        return False
    text, want = compact(ocr(cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC))), compact(value)
    return bool(want) and (want in text or SequenceMatcher(None, want, text).find_longest_match(0, len(want), 0, len(text)).size >= _FUZZY * len(want))


def make_confirm(client, timeline: ScreenTimeline, timeline_dir: Path, work_dir: Path, ocr: Optional[OcrFn]) -> Confirm:
    """The `confirm` callable for check_match, bound to this run's frames and client."""
    frames = {e.event_id: e for e in timeline.events}

    def confirm(disputed: List[Disputed]) -> Dict[Tuple[str, str], str]:
        work_dir.mkdir(parents=True, exist_ok=True)
        queued: List[Tuple[Disputed, np.ndarray, Path]] = []
        for d in disputed[: config.VLM_READ_MAX_CROPS]:
            ev = frames.get(d.event_id)
            frame = cv2.imread(str(timeline_dir / (ev.after_frame if d.side == "after" else ev.before_frame))) if ev else None
            if frame is None:
                continue
            crop = _crop(frame, timeline.edit_band)
            path = work_dir / ("verify_%s_%s.png" % (d.event_id, d.side))
            cv2.imwrite(str(path), crop)
            queued.append((d, crop, path))
        if not queued:
            return {}
        lines = ["Images, in order:"]
        for i, (d, _crop_img, _p) in enumerate(queued):
            lines.append("image %d has id %s. Candidate read: %r. Candidate workbook: %r." % (i + 1, "%s/%s" % (d.event_id, d.side), d.read, d.workbook))
        try:
            out: VerifyOutput = client.structured_chat(
                prompts.VERIFY_READ_SYSTEM, "\n".join(lines), VerifyOutput, stage="verify_read", images=[p for _d, _c, p in queued],
            )
        except Exception as exc:  # noqa: BLE001 - a failed second look leaves the disagreement unconfirmed, never accepted
            client.metrics.record_failure("verify_read_failed", str(exc))
            return {}
        by_id = {a.id: a.choice for a in out.answers}
        result: Dict[Tuple[str, str], str] = {}
        for d, crop, _p in queued:
            choice = by_id.get("%s/%s" % (d.event_id, d.side))
            if choice == "workbook" and not _supports(ocr, crop, d.workbook):
                choice = "neither"  # the model sided with the workbook but the pixels do not show its value
            if choice in ("read", "workbook", "neither"):
                result[(d.sheet, d.cell)] = choice
        return result

    return confirm
