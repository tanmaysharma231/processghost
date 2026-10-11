"""Data contract for the screen timeline (docs/22 stage 0b). Kept out of core/models/sop.py for the
same reason discovery.py is: the timeline is built from the video alone, before and independently of
the transcript, and has its own lifecycle."""
from __future__ import annotations

from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

EventType = Literal["local", "large_shift", "scene_cut"]
Box = Tuple[int, int, int, int]  # (x, y, w, h) in ORIGINAL video pixels


class ScreenEvent(BaseModel):
    event_id: str
    t_start: float  # time of the first sample that differed from the one before it
    t_end: float  # time of the last sample that still differed
    type: EventType
    changed_pct: float  # share of the downscaled frame whose pixels changed, before vs after
    coverage_pct: float  # bounding box of all changed boxes as a share of the frame area
    bboxes: List[Box] = Field(default_factory=list)
    before_frame: str  # full-resolution keyframe, path relative to the timeline's out_dir
    after_frame: str
    scene_label: Optional[str] = None  # OCR of the chrome strips, set on scene_cut only


class Scene(BaseModel):
    scene_id: str
    t_start: float
    t_end: float
    label: Optional[str] = None
    event_ids: List[str] = Field(default_factory=list)


class ScreenTimeline(BaseModel):
    video: str
    duration_s: float
    width: int
    height: int
    params: Dict[str, float] = Field(default_factory=dict)
    events: List[ScreenEvent] = Field(default_factory=list)
    scenes: List[Scene] = Field(default_factory=list)
    # One (t_s, changed_frac, n_boxes) per sample after the first -- for plotting and tuning only.
    activity: List[Tuple[float, float, int]] = Field(default_factory=list)


class RegionChange(BaseModel):
    box: Box
    before: str
    after: str


class ChangeRecord(BaseModel):
    """What one screen event changed, as OCR text, extracted without any model (core/screen/records.py).
    `chrome_*` is the OCR of the top and bottom strips of the frame (title, cell-reference / formula
    bar, tabs, status bar on a spreadsheet; URL, breadcrumb and field labels on a web page); `regions`
    is the before/after OCR of each changed box. Every claim the Reconciler makes about this event must
    be a verbatim quote of `text()`."""

    event_id: str
    t_start: float
    t_end: float
    type: str
    chrome_before: str = ""
    chrome_after: str = ""
    regions: List[RegionChange] = Field(default_factory=list)

    def text(self) -> str:
        parts = [self.chrome_before, self.chrome_after]
        for r in self.regions:
            parts.extend([r.before, r.after])
        return " | ".join(p for p in parts if p)
