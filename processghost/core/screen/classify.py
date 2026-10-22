"""Event classes by STATE change, never by pixel size (docs/26 flow plan, ADR 0010).

A dropdown can change 30% of the pixels and mean nothing; one cell edit can change 0.1% and be the whole
process. So an event is classified from what the text and the application state did, using only the
change records already extracted (no model call):

    transient    the screen changed and came back (dropdown opened and closed, hover)
    view_only    a scroll: the content moved, nothing was left changed once the shift is undone
    navigation   the application, document, sheet or page changed
    data_change  text differs before vs after at the same place, or the formula bar changed on the SAME cell
    selection    only the selected cell changed (kept as an anchor, see anchors.py)
    unclear      anything else: garbled or missing text, mixed events. Unclear is always kept.

Only positive proof drops an event (transient, view_only, and a selection nothing else points at). The
failure direction is "keep".
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List, Tuple

from core import config
from core.models.screen import ChangeRecord, EventClass, UnusedEvent
from core.screen.context import SWITCH_KINDS
from core.text_grounding import compact

_THOUSANDS = re.compile(r"(?<=\d)[,.](?=\d{3}\b)")


def norm_text(text: str) -> str:
    """Whitespace-, pipe- and case-insensitive text with thousands separators removed, so OCR spacing
    jitter and '45,000' vs '45000' are the same value."""
    return _THOUSANDS.sub("", compact(text))


def text_changed(before: str, after: str) -> bool:
    """True when `after` is a real change from `before`. Both empty is no change; text that appears or
    vanishes is a change; otherwise a similarity threshold absorbs OCR jitter."""
    a, b = norm_text(before), norm_text(after)
    if a == b:
        return False
    if not a or not b:
        return True
    return SequenceMatcher(None, a, b).ratio() < config.SCREEN_TEXT_CHANGE_MIN_RATIO


_MIN_SEGMENT = 3  # changed runs this short are OCR glyph noise, not content
_COVERED = 0.8  # share of a changed run that must also be found in the cell content to call it explained


def _covered_by(segment: str, contents: List[str]) -> bool:
    for content in contents:
        if not content:
            continue
        match = SequenceMatcher(None, segment, content).find_longest_match(0, len(segment), 0, len(content))
        if match.size >= _COVERED * len(segment):
            return True
    return False


def explained_by_selection(before: str, after: str, content_before: str, content_after: str) -> bool:
    """True when every run of text that differs between `before` and `after` is the content of the cell that was
    deselected or selected (or glyph noise). Moving the selection makes a cell's text appear in or vanish from the
    surrounding grid and formula bar without anything being edited; an edit introduces text found in neither."""
    a, b = norm_text(before), norm_text(after)
    contents = [norm_text(content_before), norm_text(content_after)]
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            continue
        for run in (a[i1:i2], b[j1:j2]):
            if len(run) >= _MIN_SEGMENT and not _covered_by(run, contents):
                return False
    return True


def _focus(rec: ChangeRecord) -> Tuple[str, str, str, str]:
    b, a = rec.surface_before, rec.surface_after
    return (
        (b.focus if b else "") or "",
        (a.focus if a else "") or "",
        (b.focus_content if b else "") or "",
        (a.focus_content if a else "") or "",
    )


def classify_record(rec: ChangeRecord) -> Tuple[EventClass, str]:
    """(class, reason) for one change record. The reason is a short ASCII string saying which rule fired."""
    if rec.transient:
        return "transient", "settled screen equals the one before (opened and closed)"
    if rec.type == "large_shift" and not rec.regions:
        return "view_only", "scroll: nothing changed once the shift is undone"
    if rec.surface_change in SWITCH_KINDS:
        return "navigation", "%s" % rec.surface_change
    changed = [r for r in rec.regions if text_changed(r.before, r.after)]
    focus_b, focus_a, content_b, content_a = _focus(rec)
    moved = bool(focus_b and focus_a and compact(focus_b) != compact(focus_a))
    if changed and moved and all(explained_by_selection(r.before, r.after, content_b, content_a) for r in changed):
        return "selection", "selected cell moved %s -> %s; the text that changed is that cell's content" % (focus_b, focus_a)
    if changed:
        return "data_change", "text differs in %d region(s)" % len(changed)
    if focus_b and focus_a and focus_b == focus_a and content_b and content_a and text_changed(content_b, content_a):
        return "data_change", "formula bar content changed on %s" % focus_a
    if moved:
        return "selection", "selected cell moved %s -> %s" % (focus_b, focus_a)
    return "unclear", "no text change and no readable selection change"


def classify_records(records: List[ChangeRecord]) -> List[ChangeRecord]:
    out = []
    for r in records:
        cls, reason = classify_record(r)
        out.append(r.model_copy(update={"event_class": cls, "class_reason": reason}))
    return out


DROPPED_CLASSES = ("transient", "view_only")


def sent_to_reconciler(records: List[ChangeRecord]) -> List[ChangeRecord]:
    """The records the Reconciler reads: everything except events proven to carry no process value.
    Selections stay in, because they may be the thing a spoken 'this'/'here' points at."""
    return [r for r in records if r.event_class not in DROPPED_CLASSES]


def unused_from_classes(records: List[ChangeRecord]) -> List[UnusedEvent]:
    return [
        UnusedEvent(event_id=r.event_id, t_s=r.t_start, event_class=r.event_class, reason=r.class_reason)
        for r in records
        if r.event_class in DROPPED_CLASSES
    ]
