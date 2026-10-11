"""Align (docs/22 stage 2b): join screen events to the transcript-built steps and flags, in plain
Python. No model calls.

Two jobs:
1. Silent-step detection. A time window alone cannot do this -- real transcript segments are ~6 s
   long, so a +/-3 s window matches almost every event. Instead each event is assigned to the step
   whose spoken span contains it (or that starts shortly AFTER it, since narration lags the action).
   Candidates are events no step owns, and events beyond SCREEN_ALIGN_MAX_EVENTS_PER_STEP inside one
   step's span. Each candidate becomes a structural flag with origin="screen", unless a flag already
   covers the same step pair.
2. Evidence attachment. Every flag gets the ids of the screen events inside its time window, which
   the Checker will use instead of fixed-interval frames.

The optional model layer from the design (show the candidate's crops and let the model look at the
adjacent set once) is NOT built; this is the deterministic layer only.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Tuple

from core import config
from core.models.screen import ScreenEvent
from core.models.sop import Flag, SOPDocument, SOPStep
from core.models.tokens import TranscriptSegment


class AlignResult(NamedTuple):
    doc: SOPDocument
    records: List[dict]  # one per event: {event_id, step_ids, flag_ids, bucket}
    said_no_event_step_ids: List[str]  # steps with narrated text but no screen event nearby


def _step_spans(doc: SOPDocument, transcript: List[TranscriptSegment]) -> Dict[str, Tuple[float, float]]:
    spans: Dict[str, Tuple[float, float]] = {}
    for step in doc.steps:
        ids = [i for a in step.attributions if a.kind == "said" for i in a.segment_ids if 0 <= i < len(transcript)]
        if ids:
            spans[step.step_id] = (min(transcript[i].start_s for i in ids), max(transcript[i].end_s for i in ids))
    return spans


def _owner(t: float, ordered: List[SOPStep], spans: Dict[str, Tuple[float, float]]) -> Optional[str]:
    for step in ordered:  # inside a spoken span
        if step.step_id in spans and spans[step.step_id][0] <= t <= spans[step.step_id][1]:
            return step.step_id
    for step in ordered:  # shortly before a span: the action came first, the words after
        if step.step_id in spans and 0 < spans[step.step_id][0] - t <= config.SCREEN_ALIGN_LEAD_S:
            return step.step_id
    return None


def _flag_window(flag: Flag, doc: SOPDocument, transcript: List[TranscriptSegment], spans) -> Tuple[float, float]:
    if flag.window_time_range:
        return flag.window_time_range
    ids = [i for i in flag.window_segment_ids if 0 <= i < len(transcript)]
    if ids:
        return min(transcript[i].start_s for i in ids), max(transcript[i].end_s for i in ids)
    anchor = flag.step_id or (flag.between_step_ids[0] if flag.between_step_ids else None)
    if anchor in spans:
        return spans[anchor]
    return 0.0, config.CHECKER_FALLBACK_WINDOW_S


def _neighbours(t: float, ordered: List[SOPStep], spans) -> Tuple[Optional[str], Optional[str]]:
    before = [s.step_id for s in ordered if s.step_id in spans and spans[s.step_id][0] <= t]
    after = [s.step_id for s in ordered if s.step_id in spans and spans[s.step_id][0] > t]
    return (before[-1] if before else None), (after[0] if after else None)


def align_events(
    doc: SOPDocument, events: List[ScreenEvent], transcript: List[TranscriptSegment], *, silent_flags: bool = True
) -> AlignResult:
    """`silent_flags=False` only attaches events to existing flags: the Reconciler (core/screen/reconcile.py)
    owns silent-step detection when it runs, and this stays the fallback when it fails."""
    ordered = sorted(doc.steps, key=lambda s: s.order)
    spans = _step_spans(doc, transcript)

    owned: Dict[str, List[ScreenEvent]] = {}
    unowned: List[ScreenEvent] = []
    owner_of: Dict[str, Optional[str]] = {}
    for ev in sorted(events, key=lambda e: e.t_start):
        owner = _owner(ev.t_start, ordered, spans)
        owner_of[ev.event_id] = owner
        (owned.setdefault(owner, []) if owner else unowned).append(ev)

    candidates: List[ScreenEvent] = list(unowned) if silent_flags else []
    for evs in owned.values():
        if silent_flags:
            candidates.extend(evs[config.SCREEN_ALIGN_MAX_EVENTS_PER_STEP :])
    candidates.sort(key=lambda e: e.t_start)

    flags = list(doc.flags)
    existing_pairs = {f.between_step_ids for f in flags if f.family == "structural" and f.between_step_ids}
    next_num = len(flags) + 1
    silent_flag_of: Dict[str, str] = {}
    for ev in candidates:
        prev_id, next_id = _neighbours(ev.t_start, ordered, spans)
        pair = (prev_id, next_id) if prev_id and next_id else None
        if pair and pair in existing_pairs:
            continue  # a Reviewer/coverage flag already asks about this gap
        flag = Flag(
            flag_id=f"FLAG-{next_num:02d}",
            family="structural",
            question=(
                f"The screen changed at about {ev.t_start:.0f}s ({ev.type.replace('_', ' ')}) but no step "
                "describes it. What action happened here?"
            ),
            severity="caution",
            window_time_range=(max(0.0, ev.t_start - config.SCREEN_ALIGN_LEAD_S), ev.t_end + config.SCREEN_ALIGN_LEAD_S),
            step_id=None if pair else (prev_id or next_id),
            between_step_ids=pair,
            origin="screen",
            event_ids=[ev.event_id],
        )
        flags.append(flag)
        silent_flag_of[ev.event_id] = flag.flag_id
        if pair:
            existing_pairs.add(pair)
        next_num += 1

    # Evidence attachment: events inside the flag's window, or up to LEAD_S before it (narration lags
    # the action). Not after it: a first run with a lead on both sides attached nearly every event to
    # some flag, which made the explained/vague buckets meaningless.
    attached: List[Flag] = []
    for flag in flags:
        if flag.origin == "screen":
            attached.append(flag)
            continue
        w0, w1 = _flag_window(flag, doc, transcript, spans)
        ids = [
            e.event_id
            for e in events
            if e.t_end >= w0 - config.SCREEN_ALIGN_LEAD_S and e.t_start <= w1  # action leads speech, never trails it
        ]
        attached.append(flag.model_copy(update={"event_ids": ids}))

    flagged_by: Dict[str, List[str]] = {}
    for f in attached:
        for eid in f.event_ids:
            flagged_by.setdefault(eid, []).append(f.flag_id)
    records = []
    for ev in sorted(events, key=lambda e: e.t_start):
        if ev.event_id in silent_flag_of:
            bucket = "silent"
        elif any(fid for fid in flagged_by.get(ev.event_id, [])):
            bucket = "vague"
        else:
            bucket = "explained"
        records.append(
            {
                "event_id": ev.event_id,
                "step_ids": [owner_of[ev.event_id]] if owner_of.get(ev.event_id) else [],
                "flag_ids": flagged_by.get(ev.event_id, []),
                "bucket": bucket,
            }
        )

    no_event = [s.step_id for s in ordered if s.step_id in spans and not owned.get(s.step_id)]
    return AlignResult(doc.model_copy(update={"flags": attached}), records, no_event)
