"""Investigation index (ADR 0011): the three independent triggers, merged, with coverage accounting.

  A  context transition   a screen event that changed application / sheet / page (or a scene cut)
  B  transcript           a flag raised from what was SAID, with the screen events attached to it
  C  unexplained activity a screen event no narrated step accounts for

Triggers are candidates, not findings. Overlapping triggers share one `Investigation` so the same stretch
is not examined twice; each keeps its own reason. After the Checker has run, `update_status` records
whether anything actually looked at each investigation, so a flag cap or a spend budget shows up as
"skipped", never as "nothing there". Pure Python: no model call, no I/O.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from core.models.screen import ChangeRecord, Investigation, ScreenEvent, ScreenTimeline, TriggerReason
from core.models.sop import Flag, ScreenCoverage, SOPDocument
from core.models.tokens import TranscriptSegment
from core.screen.context import SWITCH_KINDS
from core.screen.evidence_id import event_evidence_ids

_Window = Tuple[float, float]


def explained_event_ids(doc: SOPDocument, align_records: Optional[List[dict]] = None) -> Set[str]:
    """Events some narrated step accounts for: those the Reconciler grounded to a step, plus those Align
    left in its 'explained' bucket (owned by a step and covered by no flag)."""
    ids = {e for s in doc.steps for oc in s.observed_changes for e in oc.event_ids}
    for rec in align_records or []:
        if rec.get("bucket") == "explained":
            ids.add(rec["event_id"])
    return ids


def _flag_window(flag: Flag, transcript: List[TranscriptSegment], events: Dict[str, ScreenEvent]) -> Optional[_Window]:
    if flag.window_time_range:
        return flag.window_time_range
    seg = [transcript[i] for i in flag.window_segment_ids if 0 <= i < len(transcript)]
    if seg:
        return min(s.start_s for s in seg), max(s.end_s for s in seg)
    evs = [events[e] for e in flag.event_ids if e in events]
    if evs:
        return min(e.t_start for e in evs), max(e.t_end for e in evs)
    return None


def _a_detail(rec: Optional[ChangeRecord], ev: ScreenEvent) -> str:
    if rec is None or rec.surface_change is None:
        return ev.type
    where = rec.surface_after.location if rec.surface_after and rec.surface_after.location else ""
    detail = rec.surface_change + (f" to {where}" if where else "")
    if rec.speech_clue is not None:
        detail += " (named in speech)" if rec.speech_clue.found else " (not named in speech)"
    return detail


def _is_context_transition(ev: ScreenEvent, rec: Optional[ChangeRecord]) -> bool:
    return ev.type == "scene_cut" or (rec is not None and rec.surface_change in SWITCH_KINDS)


class _Item:
    def __init__(self, reason: TriggerReason, event_ids: List[str], window: _Window, flag_id: Optional[str] = None) -> None:
        self.reason, self.event_ids, self.window, self.flag_id = reason, set(event_ids), window, flag_id


def _group_by_shared_events(items: List[_Item]) -> List[List[_Item]]:
    groups: List[List[_Item]] = []
    for item in items:
        hits = [g for g in groups if any(item.event_ids & other.event_ids for other in g)]
        merged = [item] + [o for g in hits for o in g]
        groups = [g for g in groups if g not in hits] + [merged]
    return groups


def build_investigations(
    timeline: ScreenTimeline,
    records: List[ChangeRecord],
    doc: SOPDocument,
    transcript: List[TranscriptSegment],
    explained: Set[str],
) -> List[Investigation]:
    events = {e.event_id: e for e in timeline.events}
    recs = {r.event_id: r for r in records}
    ordered = sorted(timeline.events, key=lambda e: e.t_start)

    items: List[_Item] = []
    for ev in ordered:  # Trigger A and Trigger C come straight from the screen index
        rec = recs.get(ev.event_id)
        win = (ev.t_start, ev.t_end)
        if _is_context_transition(ev, rec):
            items.append(_Item(TriggerReason(kind="A_context", source_id=ev.event_id, detail=_a_detail(rec, ev)), [ev.event_id], win))
        if ev.event_id not in explained:
            note = "interaction with no committed change" if ev.transient else "no narrated step accounts for it"
            items.append(_Item(TriggerReason(kind="C_unexplained", source_id=ev.event_id, detail=note), [ev.event_id], win))

    eventless: List[_Item] = []
    for flag in doc.flags:  # Trigger B comes from flags raised by the transcript
        if flag.origin == "screen" or (flag.origin == "reconcile" and flag.family == "structural" and flag.event_ids):
            continue  # silent-step flags restate Trigger C; they are attached to its investigation below
        ev_ids = [e for e in flag.event_ids if e in events]
        window = _flag_window(flag, transcript, events)
        if window is None:
            continue
        reason = TriggerReason(kind="B_transcript", source_id=flag.flag_id, detail=flag.question[:160])
        if ev_ids:
            # One item per attached event, so a flag adds its reason to each event's investigation instead of
            # bridging them: Align attaches every event near a wide flag window, and bridging made a single
            # real run's investigations collapse into one spanning the whole recording.
            items.extend(_Item(reason, [e], (events[e].t_start, events[e].t_end), flag.flag_id) for e in ev_ids)
        else:
            eventless.append(_Item(reason, [], window, flag.flag_id))

    invs: List[Investigation] = []
    for group in _group_by_shared_events(items):
        evs = sorted((events[e] for item in group for e in item.event_ids), key=lambda e: e.t_start)
        evs = list({e.event_id: e for e in evs}.values())
        invs.append(
            Investigation(
                inv_id="",
                t_start=min(i.window[0] for i in group),
                t_end=max(i.window[1] for i in group),
                event_ids=[e.event_id for e in evs],
                context_id=evs[0].context_id if evs else None,
                triggers=_dedupe([i.reason for i in group]),
                flag_ids=sorted({i.flag_id for i in group if i.flag_id}),
            )
        )

    def context_at(t: float) -> Optional[str]:
        if not ordered:
            return None
        before = [e for e in ordered if e.t_start <= t]
        return (before[-1] if before else ordered[0]).context_id

    for item in eventless:  # the transcript asked, but no screen change was attached to its flag
        ctx = context_at(item.window[0])
        host = next(
            (v for v in invs if v.context_id == ctx and v.t_start <= item.window[1] and item.window[0] <= v.t_end), None
        )
        if host is not None:
            host.triggers = _dedupe(host.triggers + [item.reason])
            host.flag_ids = sorted(set(host.flag_ids) | {item.flag_id})
        else:
            invs.append(
                Investigation(
                    inv_id="", t_start=item.window[0], t_end=item.window[1], context_id=ctx,
                    triggers=[item.reason], flag_ids=[item.flag_id],
                )
            )

    # Silent-step flags and any other flag citing an investigation's events belong to it.
    for inv in invs:
        cited = {f.flag_id for f in doc.flags if set(f.event_ids) & set(inv.event_ids)}
        inv.flag_ids = sorted(set(inv.flag_ids) | cited)

    invs.sort(key=lambda v: (v.t_start, v.t_end))
    return [v.model_copy(update={"inv_id": f"inv-{n:03d}"}) for n, v in enumerate(invs, start=1)]


def _dedupe(reasons: List[TriggerReason]) -> List[TriggerReason]:
    seen, out = set(), []
    for r in reasons:
        key = (r.kind, r.source_id)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return sorted(out, key=lambda r: (r.kind, r.source_id))


def update_status(
    invs: List[Investigation], doc: SOPDocument, explained: Set[str], *, budget_exceeded: bool = False
) -> List[Investigation]:
    """examined: a Checker verdict exists for one of its flags, or a step's grounded observation covers one
    of its events. Otherwise skipped (flag cap or spend budget) or unexamined (nothing reached it)."""
    status_of = {f.flag_id: f.status for f in doc.flags}
    out: List[Investigation] = []
    for inv in invs:
        statuses = [status_of[i] for i in inv.flag_ids if i in status_of]
        if any(s in ("resolved", "contradicted", "unresolvable") for s in statuses) or any(e in explained for e in inv.event_ids):
            new = "examined"
        elif "unchecked" in statuses:
            new = "skipped_budget" if budget_exceeded else "skipped_cap"
        else:
            new = "unexamined"
        out.append(inv.model_copy(update={"status": new}))
    return out


def attach_evidence_ids(invs: List[Investigation], timeline: ScreenTimeline) -> List[Investigation]:
    """Cite the saved keyframes of each investigation's events by stable id."""
    events = {e.event_id: e for e in timeline.events}
    return [
        v.model_copy(
            update={"evidence_ids": [i for e in v.event_ids if e in events for i in event_evidence_ids(timeline.video_sha, events[e])]}
        )
        for v in invs
    ]


def coverage_of(invs: List[Investigation]) -> ScreenCoverage:
    count = lambda s: sum(1 for v in invs if v.status == s)  # noqa: E731
    return ScreenCoverage(
        total=len(invs),
        examined=count("examined"),
        skipped_budget=count("skipped_budget"),
        skipped_cap=count("skipped_cap"),
        unexamined=count("unexamined"),
        not_examined_spans=[(round(v.t_start, 1), round(v.t_end, 1)) for v in invs if v.status != "examined"],
    )
