"""Context map (ADR 0013, Trigger A): the application/sheet/page contexts a recording passes through.

A context is created when the surface locator changes (or a scene cut shows something unrecognised) and is
REUSED when the same context reappears, so each is described once and later crops can be read against it.
Observations only: the locator plus capped OCR of the first full frame seen there. Hypotheses such as
"this column is the matching key" are deliberately not stored (design doc ADR-009).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2

from core import config
from core.models.screen import ChangeRecord, ContextMap, Locator, ScreenEvent, ScreenTimeline
from core.screen.ocr import OcrFn
from core.text_grounding import compact

SWITCH_KINDS = {"app_switch", "document_switch", "sheet_switch", "page_navigation"}


def context_key(loc: Locator) -> Tuple:
    """Identity of a context. A spreadsheet is its document plus sheet position (stable under OCR noise);
    a web page is host, path and title; anything unrecognised is its first title fragment."""
    if loc.surface == "spreadsheet":
        return ("spreadsheet", compact(loc.document), loc.index if loc.index is not None else compact(loc.location))
    if loc.surface == "web":
        return ("web", compact(loc.document), compact(loc.location), compact(loc.title))
    return ("generic", compact(loc.title)[:40])


class _Builder:
    def __init__(self, timeline_dir: Path, ocr: OcrFn) -> None:
        self.timeline_dir = timeline_dir
        self.ocr = ocr
        self.by_key: Dict[Tuple, ContextMap] = {}
        self.current: Optional[ContextMap] = None

    def enter(self, loc: Optional[Locator], t: float, frame: str) -> Optional[ContextMap]:
        if loc is None:
            return self.current
        key = context_key(loc)
        if self.current is not None and context_key(self.current.locator) == key:
            return self.current
        ctx = self.by_key.get(key)
        if ctx is None:
            ctx = ContextMap(
                context_id=f"ctx-{len(self.by_key) + 1:03d}", locator=loc, labels=self._labels(frame), t_first=t, t_last=t
            )
            self.by_key[key] = ctx
        else:
            ctx.seen_count += 1
        self.current = ctx
        return ctx

    def _labels(self, frame: str) -> str:
        img = cv2.imread(str(self.timeline_dir / frame))
        text = self.ocr(img) if img is not None else ""
        return text[: config.CONTEXT_MAX_OCR_CHARS]


def build_contexts(
    timeline: ScreenTimeline, records: List[ChangeRecord], timeline_dir: Path, ocr: OcrFn
) -> Tuple[List[ContextMap], ScreenTimeline, List[ChangeRecord]]:
    """Walk the events in time order. Returns the contexts, the timeline with each event's `context_id`
    set, and the records with theirs. An event is a context switch when its surface locators differ, or
    it is a scene cut whose locator is not recognised (then the after-locator decides)."""
    rec_by_id = {r.event_id: r for r in records}
    builder = _Builder(timeline_dir, ocr)
    new_events: List[ScreenEvent] = []
    new_records: Dict[str, ChangeRecord] = {}

    for ev in sorted(timeline.events, key=lambda e: e.t_start):
        rec = rec_by_id.get(ev.event_id)
        if rec is None:
            new_events.append(ev)
            continue
        if builder.current is None:  # the context the recording starts in, read from the first event's before frame
            builder.enter(rec.surface_before, 0.0, ev.before_frame)
        if rec.surface_change in SWITCH_KINDS or (ev.type == "scene_cut" and rec.surface_change == "unknown"):
            builder.enter(rec.surface_after, ev.t_start, ev.after_frame)
        ctx = builder.current
        if ctx is not None:
            ctx.t_last = max(ctx.t_last, ev.t_end)
            ctx.event_ids.append(ev.event_id)
        cid = ctx.context_id if ctx else None
        new_events.append(ev.model_copy(update={"context_id": cid}))
        new_records[ev.event_id] = rec.model_copy(update={"context_id": cid})

    contexts = sorted(builder.by_key.values(), key=lambda c: c.context_id)
    return contexts, timeline.model_copy(update={"events": new_events}), [new_records.get(r.event_id, r) for r in records]
