"""Selection anchors (docs/26 flow plan): a click on a cell is evidence for what is said or done near it.

A selection is kept as an anchor instead of being dropped or turned into a step. Two independent signals
make it a confirmed anchor with a role:

    speech   a pointer word ("this", "here", ...) or the cell's own value is spoken near the event
             (window reuses the speech-clue constants: narration lags the action)
    value    a LATER data change puts the same value somewhere (copy source -> paste target)

Both -> confirmed, role "source". One -> a candidate, shown as "possibly <cell>" and never as fact. None ->
the selection is listed as unused screen activity. If the value could belong to more than one change, or
two anchors hold the same value, the value signal is withheld: ambiguity yields candidates, never a guess.

No model call. Timestamps and OCR text only.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from core import config
from core.models.screen import ChangeRecord, SelectionAnchor, UnusedEvent
from core.screen.classify import norm_text
from core.screen.speech_clue import timed_words

_MAX_VALUE_CHARS = 24  # a long formula is not a value someone speaks or pastes


def _anchor_content(rec: ChangeRecord) -> str:
    loc = rec.surface_after
    return (loc.focus_content if loc else "") or ""


def _anchor_focus(rec: ChangeRecord) -> str:
    loc = rec.surface_after
    return (loc.focus if loc else "") or ""


def _is_anchor_source(rec: ChangeRecord) -> bool:
    """Selections, and navigations that landed on a readable cell (a sheet switch plus a click inside one second)."""
    if rec.event_class not in ("selection", "navigation"):
        return False
    return bool(_anchor_focus(rec))


def _value_key(content: str) -> str:
    key = norm_text(content)
    return key if 2 <= len(key) <= _MAX_VALUE_CHARS else ""


def _speech_signal(rec: ChangeRecord, key: str, words: List[Tuple[str, float]]) -> Optional[str]:
    lo = rec.t_start - config.SURFACE_SPEECH_WINDOW_BEFORE_S
    hi = rec.t_end + config.SURFACE_SPEECH_WINDOW_AFTER_S
    heard = [w for w, t in words if lo <= t <= hi]
    if key and key in heard:
        return "speech:value:%s" % key
    for w in heard:
        if w in config.ANCHOR_POINTER_WORDS:
            return "speech:pointer:%s" % w
    return None


def _value_targets(rec: ChangeRecord, key: str, records: List[ChangeRecord]) -> List[str]:
    """Later data changes in which the anchor's value newly appears."""
    if not key:
        return []
    out = []
    for other in records:
        if other.event_id == rec.event_id or other.event_class != "data_change":
            continue
        if not (rec.t_end <= other.t_start <= rec.t_end + config.ANCHOR_VALUE_WINDOW_S):
            continue
        if any(key in norm_text(r.after) and key not in norm_text(r.before) for r in other.regions):
            out.append(other.event_id)
    return out


def build_anchors(records: List[ChangeRecord], segments_raw: List[dict]) -> Tuple[List[SelectionAnchor], List[UnusedEvent]]:
    """(anchors, unused). `records` must already carry event_class. Anchors are in time order."""
    words = timed_words(segments_raw)
    sources = [r for r in sorted(records, key=lambda r: r.t_start) if _is_anchor_source(r)]
    held = {}  # value key -> number of anchors holding it
    for r in sources:
        k = _value_key(_anchor_content(r))
        if k:
            held[k] = held.get(k, 0) + 1

    anchors: List[SelectionAnchor] = []
    unused: List[UnusedEvent] = []
    for r in sources:
        content = _anchor_content(r)
        key = _value_key(content)
        signals: List[str] = []
        target: Optional[str] = None

        spoken = _speech_signal(r, key, words)
        if spoken:
            signals.append(spoken)

        targets = _value_targets(r, key, records)
        if len(targets) == 1 and held.get(key, 0) == 1:
            signals.append("value:%s" % targets[0])
            target = targets[0]
        elif targets:
            signals.append("value_ambiguous")  # informational: does not count toward confirmation

        has_speech = any(s.startswith("speech:") for s in signals)
        has_value = any(s.startswith("value:") for s in signals)
        if has_value:
            role = "source"
        elif has_speech:
            role = "pointer" if any(s.startswith("speech:pointer") for s in signals) else "check"
        else:
            role = None
        anchors.append(
            SelectionAnchor(
                event_id=r.event_id, t_s=r.t_start, focus=_anchor_focus(r), content=content, role=role,
                signals=signals, confirmed=has_speech and has_value, target_event_id=target,
            )
        )
        if role is None and r.event_class == "selection":
            unused.append(
                UnusedEvent(
                    event_id=r.event_id, t_s=r.t_start, event_class="selection",
                    reason="selection with no speech or value link",
                )
            )
    return anchors, unused


def describe(anchor: SelectionAnchor) -> str:
    """One ASCII line for reports: a candidate is worded as such, never as fact."""
    where = anchor.focus or "a cell"
    if anchor.confirmed:
        return "%s (%s, value %s)" % (where, anchor.role, anchor.content or "unread")
    return "possibly %s" % where
