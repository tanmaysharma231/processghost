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
    # Adaptive-evidence additions (docs/26). All defaulted so older screen_events.json files still load.
    transient: bool = False  # the screen changed and then returned (dropdown opened and closed): no committed change
    peak_frame: Optional[str] = None  # the most-changed sample inside a transient event, relative to out_dir
    residual_bboxes: List[Box] = Field(default_factory=list)  # large_shift only: changes left after undoing the scroll
    context_id: Optional[str] = None  # the ContextMap this event happened in (core/screen/context.py)

    def region_boxes(self) -> List[Box]:
        """The boxes worth reading as changes. For a scroll the raw boxes cover the whole moved page, so
        only what changed beyond the scroll counts; for everything else it is the raw boxes."""
        return self.residual_bboxes if self.type == "large_shift" else self.bboxes


class Scene(BaseModel):
    scene_id: str
    t_start: float
    t_end: float
    label: Optional[str] = None
    event_ids: List[str] = Field(default_factory=list)


class ScreenTimeline(BaseModel):
    video: str
    video_sha: Optional[str] = None  # content fingerprint of the video (core/screen/evidence_id.py); keys evidence ids
    duration_s: float
    width: int
    height: int
    params: Dict[str, float] = Field(default_factory=dict)
    events: List[ScreenEvent] = Field(default_factory=list)
    scenes: List[Scene] = Field(default_factory=list)
    edit_band: Optional[Tuple[int, int]] = None  # (y0, y1) of the name-box / formula-bar row in original pixels, when found (core/screen/edit_band.py)
    # One (t_s, changed_frac, n_boxes) per sample after the first -- for plotting and tuning only.
    activity: List[Tuple[float, float, int]] = Field(default_factory=list)


class RegionChange(BaseModel):
    box: Box
    before: str
    after: str


ReadBy = Literal["ocr", "ocr_retry", "vlm"]
SurfaceKind = Literal["spreadsheet", "web", "generic"]
SurfaceChangeKind = Literal[
    "app_switch", "document_switch", "sheet_switch", "page_navigation", "same_surface_edit", "unknown"
]


class Locator(BaseModel):
    """Where on which application a frame is, read from its chrome text with no model. Observation only:
    every non-empty field is a substring (modulo whitespace) of the OCR text it came from, and `evidence`
    lists those substrings. `surface` "generic" means no profile recognised the application."""

    surface: SurfaceKind = "generic"
    app: str = ""  # e.g. the spreadsheet or browser product name, when legible
    document: str = ""  # workbook file name, or site host
    location: str = ""  # sheet name, or page path
    index: Optional[int] = None  # 1-based sheet position when legible ("Sheet 2 of 3"); more stable than the name under OCR noise
    title: str = ""  # page or window title
    focus: str = ""  # selected cell reference, when legible
    focus_content: str = ""  # formula-bar text, when legible
    evidence: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    read_by: Dict[str, ReadBy] = Field(default_factory=dict)  # field -> how it was read; absent means plain OCR


class SpeechClue(BaseModel):
    """Corroboration only (never a gate): did the speaker name this event's destination near the time it
    happened? `lag_s` is spoken time minus event start (positive = spoken after the action)."""

    found: bool = False
    matched: List[str] = Field(default_factory=list)  # the spoken words that matched the locator's name
    nav_words: List[str] = Field(default_factory=list)  # navigation verbs heard in the window
    lag_s: Optional[float] = None


class ContextMap(BaseModel):
    """One application/sheet/page context the recording passes through. Reused when the same context
    reappears, so it is described once. Observations only: `labels` is capped OCR of the first full frame
    seen in the context (column headers, field labels, record ids). Relationships between them are
    hypotheses and are NOT stored here."""

    context_id: str
    locator: Locator
    labels: str = ""
    evidence_id: Optional[str] = None  # EvidenceRef id of the full frame the labels were read from
    t_first: float = 0.0
    t_last: float = 0.0
    seen_count: int = 1  # times the recording entered this context
    event_ids: List[str] = Field(default_factory=list)


class EvidenceRef(BaseModel):
    """A retrievable artifact with a stable id (core/screen/retrieve.py). Findings cite these."""

    evidence_id: str
    kind: Literal["context_frame", "crop", "compare", "keyframe"]
    t_s: float
    t_end: Optional[float] = None
    box: Optional[Box] = None
    path: str  # relative to the retriever's out_dir
    event_id: Optional[str] = None
    video_sha: Optional[str] = None


class Refusal(BaseModel):
    """Returned instead of evidence when the inspection budget is spent. Recorded, never silent: a refusal
    is a limit on what was looked at, not evidence that nothing was there."""

    refused: Literal[True] = True
    tool: str
    reason: str


class RegionsResult(BaseModel):
    evidence_id: str
    t1: float
    t2: float
    changed_frac: float
    boxes: List[Box] = Field(default_factory=list)  # every changed box, original pixels
    groups: List[Box] = Field(default_factory=list)  # the same boxes merged by proximity, never dropped by size
    frames: List[EvidenceRef] = Field(default_factory=list)  # only when frames were asked for


class EpisodeResult(BaseModel):
    evidence_id: str
    t_start: float
    t_end: float
    event_ids: List[str] = Field(default_factory=list)
    transcript: List[str] = Field(default_factory=list)  # spoken text overlapping the interval
    frames: List[EvidenceRef] = Field(default_factory=list)  # keyframes, only at detail "keyframes"


TriggerKind = Literal["A_context", "B_transcript", "C_unexplained"]
InvestigationStatus = Literal["examined", "unexamined", "skipped_budget", "skipped_cap"]


class TriggerReason(BaseModel):
    """Why a stretch of the recording is worth a closer look. A trigger is a candidate, not a finding."""

    kind: TriggerKind
    source_id: str  # event id (A, C) or flag id (B)
    detail: str = ""


class Investigation(BaseModel):
    """One stretch of the recording that one or more triggers asked to look at, merged so overlapping
    triggers share a single inspection. `status` records whether anything actually looked at it, so a
    budget or cap cut is never mistaken for 'nothing there'."""

    inv_id: str
    t_start: float
    t_end: float
    event_ids: List[str] = Field(default_factory=list)  # empty: the transcript asked, no screen change was located
    context_id: Optional[str] = None
    triggers: List[TriggerReason] = Field(default_factory=list)
    flag_ids: List[str] = Field(default_factory=list)
    status: InvestigationStatus = "unexamined"
    evidence_ids: List[str] = Field(default_factory=list)


EventClass = Literal["transient", "view_only", "selection", "navigation", "data_change", "unclear"]
AnchorRole = Literal["pointer", "source", "check"]


class UnusedEvent(BaseModel):
    """A screen event that was classified as carrying no process value and so became no step. Listed in the
    report with its reason, never silently dropped."""

    event_id: str
    t_s: float
    event_class: EventClass
    reason: str = ""


class SelectionAnchor(BaseModel):
    """A cell/item selection kept as a pointer for what is said or done near it, not as a step of its own.
    `confirmed` needs two independent signals (time plus a pointer word, or time plus a value that shows up
    in a later data change); one signal leaves it a candidate ("possibly <focus>"), never a statement."""

    event_id: str
    t_s: float
    focus: str = ""
    content: str = ""
    role: Optional[AnchorRole] = None
    signals: List[str] = Field(default_factory=list)
    confirmed: bool = False
    target_event_id: Optional[str] = None  # the data change that received this anchor's value, when that is the signal


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
    # Filled by core/screen/records.py, context.py and speech_clue.py (all defaulted for older files).
    transient: bool = False
    surface_before: Optional[Locator] = None
    surface_after: Optional[Locator] = None
    surface_change: Optional[SurfaceChangeKind] = None
    context_id: Optional[str] = None
    speech_clue: Optional[SpeechClue] = None
    # Filled by core/screen/classify.py from the state change (never from pixel size).
    event_class: Optional[EventClass] = None
    class_reason: str = ""

    def text(self) -> str:
        parts = [self.chrome_before, self.chrome_after]
        for r in self.regions:
            parts.extend([r.before, r.after])
        return " | ".join(p for p in parts if p)
