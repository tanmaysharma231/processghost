"""Reconciler (docs/24): map narrated steps onto screen events and say what changed for EVERY step,
not only the ones the Reviewer was unsure about.

One batched model call per run reads the steps (with word-timed speech) and one OCR change record per
event, and returns, per step: the events that realise it, a "what changed" sentence with verbatim
quotes from those records, and the questions that remain unspecified; per event: the step it belongs
to (or none); and a check of any claimed consequence. This module builds that input and merges the
answer into the SOPDocument -- and trusts none of it unchecked:

- a "what changed" sentence is kept only if at least one of its quotes is a verbatim substring (after
  removing the whitespace/pipe noise OCR adds) of the record of the event it names; otherwise it is
  dropped and the step counts as "said, not shown";
- a claim marked supported needs such a quote, or it is downgraded to unclear;
- every event no step explains becomes a silent-step flag (left open, so the Checker can describe it).

Flags it creates carry origin="reconcile". Questions that remain after reading the records, unsupported
claims and said-but-not-shown steps are created already "unresolvable": the Reconciler has read the
evidence, so there is nothing for the Checker to look at, and they go to the recorder as questions.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from core import config
from core.models.reconcile import EventAssignment, ReconcilerOutput
from core.models.screen import ChangeRecord, SelectionAnchor
from core.models.sop import Attribution, Flag, ObservedChange, SlotFill, SOPDocument, SOPStep
from core.slots import add_slot_flags, fill_slot, open_slots
from core.models.tokens import TranscriptSegment
from core.screen.align import _neighbours, _step_spans
from core.text_grounding import compact, quote_in_ocr


def _timed_words(segments_raw: List[dict], seg_ids: List[int]) -> str:
    out: List[str] = []
    for i in seg_ids:
        if not (0 <= i < len(segments_raw)):
            continue
        seg = segments_raw[i]
        words = seg.get("words") or []
        if words:
            out.append(" ".join(f"{w['word'].strip()}@{w['start']:.1f}" for w in words))
        else:
            out.append(f"[{seg.get('start', 0):.1f}-{seg.get('end', 0):.1f}s] {seg.get('text', '').strip()}")
    return " ".join(out)


def build_reconciler_input(
    doc: SOPDocument, records: List[ChangeRecord], segments_raw: List[dict], anchors: Optional[List[SelectionAnchor]] = None
) -> str:
    steps = []
    for s in sorted(doc.steps, key=lambda s: s.order):
        seg_ids = [i for a in s.attributions if a.kind == "said" for i in a.segment_ids]
        steps.append(
            {
                "step_id": s.step_id,
                "order": s.order,
                "title": s.title,
                "instruction": s.instruction,
                "spoken": _timed_words(segments_raw, seg_ids),
                "kind": s.kind,
                "open_slots": open_slots(s),
            }
        )
    by_event = {a.event_id: a for a in anchors or []}
    events = [_event_payload(r, by_event.get(r.event_id)) for r in records]
    return str({"steps": steps, "events": events})


def _event_payload(r: ChangeRecord, anchor: Optional[SelectionAnchor] = None) -> dict:
    payload = {
        "event_id": r.event_id,
        "t_start": r.t_start,
        "t_end": r.t_end,
        "type": r.type,
        "chrome_before": r.chrome_before,
        "chrome_after": r.chrome_after,
        "regions": [{"before": g.before, "after": g.after} for g in r.regions],
    }
    # Structured locator facts, present only when the surface stage produced them. Each value is a
    # substring of the chrome text above, so quoting it still grounds against the record.
    if r.surface_change is not None:
        payload["surface_change"] = r.surface_change
    if r.surface_after is not None and r.surface_after.surface != "generic":
        payload["location_after"] = {
            k: v for k, v in r.surface_after.model_dump(include={"app", "document", "location", "focus", "focus_content"}).items() if v
        }
    if r.transient:
        payload["transient"] = True
    if r.event_class is not None:
        payload["event_class"] = r.event_class
    if anchor is not None:
        payload["anchor"] = {"cell": anchor.focus, "role": anchor.role, "confirmed": anchor.confirmed}
    if r.speech_clue is not None:
        payload["speech_clue"] = {"named_in_speech": r.speech_clue.found, "lag_s": r.speech_clue.lag_s}
    return payload


def _window(step: SOPStep, spans: Dict[str, Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    return spans.get(step.step_id)


SHOWN_CLASSES = ("navigation", "data_change")
_TITLE_MAX_CHARS = 70


def _shown_step(r: ChangeRecord, a: Optional[EventAssignment], taken_ids: set) -> Optional[SOPStep]:
    """A step for an event nobody narrated: only a navigation or data change whose action sentence is backed
    by at least one verbatim quote from THIS event's record. Anything else stays a silent-change flag."""
    if r.event_class not in SHOWN_CLASSES or a is None or not a.action.strip():
        return None
    quotes = [q.quote for q in a.evidence if q.event_id == r.event_id and quote_in_ocr(q.quote, r.text())]
    if not quotes:
        return None
    action = " ".join(a.action.split())
    n = 1
    while "step-s%02d" % n in taken_ids:
        n += 1
    title = action if len(action) <= _TITLE_MAX_CHARS else action[: _TITLE_MAX_CHARS - 3].rstrip() + "..."
    kind = "navigate" if r.event_class == "navigation" else "edit"
    focus = r.surface_after.focus if r.surface_after else ""
    slots = {"target": SlotFill(value=focus, by="shown", event_ids=[r.event_id])} if kind == "edit" and focus else {}
    return SOPStep(
        step_id="step-s%02d" % n,
        order=0,  # renumbered once every shown step is placed
        title=title,
        instruction=action,
        attributions=[Attribution(kind="shown", t_s=r.t_start, event_ids=[r.event_id])],
        observed_changes=[ObservedChange(text=action, event_ids=[r.event_id], t_s=r.t_start, quotes=quotes)],
        kind=kind,
        slots=slots,
    )


def _step_time(step: SOPStep, spans: Dict[str, Tuple[float, float]]) -> Optional[float]:
    if step.step_id in spans:
        return spans[step.step_id][0]
    shown = [a.t_s for a in step.attributions if a.kind == "shown"]
    return min(shown) if shown else None


def _insert_by_time(steps: List[SOPStep], shown: List[SOPStep], spans: Dict[str, Tuple[float, float]]) -> List[SOPStep]:
    """Place each shown step after the last timed step that starts at or before it, then renumber 1..n."""
    if not shown:
        return steps
    out = sorted(steps, key=lambda s: s.order)
    for new in sorted(shown, key=lambda s: s.attributions[0].t_s):
        t = new.attributions[0].t_s
        at = 0
        for i, s in enumerate(out):
            st = _step_time(s, spans)
            if st is not None and st <= t:
                at = i + 1
        out.insert(at, new)
    return [s.model_copy(update={"order": i + 1}) for i, s in enumerate(out)]


def merge_reconciler_output(
    doc: SOPDocument, output: ReconcilerOutput, records: List[ChangeRecord], transcript: List[TranscriptSegment]
) -> SOPDocument:
    rec_by_id = {r.event_id: r for r in records}
    steps_by_id = {s.step_id: s for s in doc.steps}
    ordered = sorted(doc.steps, key=lambda s: s.order)
    spans = _step_spans(doc, transcript)

    new_steps = dict(steps_by_id)
    flags = list(doc.flags)
    existing_questions = {(f.step_id, compact(f.question)) for f in flags}
    next_num = len(flags) + 1

    def add_flag(**kw) -> None:
        nonlocal next_num
        flags.append(Flag(flag_id=f"FLAG-{next_num:02d}", origin="reconcile", **kw))
        next_num += 1

    explained_events: set = set()
    seen_steps: set = set()
    slot_fills: List[Tuple[str, str, SlotFill]] = []
    for rec in output.steps:
        step = steps_by_id.get(rec.step_id)
        if step is None or rec.step_id in seen_steps:
            continue
        seen_steps.add(rec.step_id)
        win = _window(step, spans)

        # A quote counts only if it is in the record of an event the model declared for THIS step: a
        # sentence about one event supported by text from another is not evidence for it.
        declared = set(rec.event_ids)
        grounded = [
            q
            for q in rec.evidence
            if q.event_id in declared and q.event_id in rec_by_id and quote_in_ocr(q.quote, rec_by_id[q.event_id].text())
        ]
        if rec.what_changed.strip() and grounded:
            ev_ids = sorted({q.event_id for q in grounded})
            explained_events.update(ev_ids)
            new_steps[step.step_id] = step.model_copy(
                update={
                    "observed_changes": step.observed_changes
                    + [
                        ObservedChange(
                            text=rec.what_changed.strip(),
                            event_ids=ev_ids,
                            t_s=min(rec_by_id[e].t_start for e in ev_ids),
                            quotes=[q.quote for q in grounded],
                        )
                    ]
                }
            )
        elif win is not None:
            note = " The model named events but none of its quotes were found in their records." if rec.event_ids or rec.what_changed.strip() else ""
            key = (step.step_id, compact("said-not-shown"))
            if key not in existing_questions:
                existing_questions.add(key)
                add_flag(
                    family="structural",
                    question=(
                        f"The step '{step.title}' was narrated but no change on screen matches it. "
                        f"Was it done off screen, or did it not take effect?{note}"
                    ),
                    severity="caution",
                    window_time_range=win,
                    step_id=step.step_id,
                    status="unresolvable",
                    finding_kind="unsupported_assertion",
                )

        for claim in rec.slot_fills:
            ev_ids = sorted({q.event_id for q in claim.evidence if q.event_id in rec_by_id and quote_in_ocr(q.quote, rec_by_id[q.event_id].text())})
            if claim.slot in open_slots(step) and claim.value.strip() and ev_ids:
                slot_fills.append(
                    (step.step_id, claim.slot, SlotFill(value=claim.value.strip(), by="shown", event_ids=ev_ids,
                                                         quote=next(q.quote for q in claim.evidence if q.event_id == ev_ids[0])))
                )

        added = 0
        for q in rec.unspecified:
            key = (step.step_id, compact(q.question))
            if not q.question.strip() or key in existing_questions or win is None:
                continue
            if added >= config.RECONCILER_MAX_QUESTIONS_PER_STEP:
                break
            added += 1
            existing_questions.add(key)
            add_flag(
                family="ambiguity",
                question=q.question.strip(),
                severity=q.severity,
                window_time_range=win,
                step_id=step.step_id,
                event_ids=[e for e in rec.event_ids if e in rec_by_id],
                status="unresolvable",
                finding_kind="unresolved_requirement",
            )

    for c in output.claims:
        step = steps_by_id.get(c.step_id)
        win = _window(step, spans) if step else None
        if step is None or win is None:
            continue
        shown = c.supported == "yes" and any(
            q.event_id in rec_by_id and quote_in_ocr(q.quote, rec_by_id[q.event_id].text()) for q in c.evidence
        )
        if shown:
            continue
        severity = "blocking" if c.supported == "no" else "caution"
        key = (step.step_id, compact(c.claim))
        if key in existing_questions:
            continue
        existing_questions.add(key)
        add_flag(
            family="ambiguity",
            question=(
                f"The narration says \"{c.claim.strip()}\" but no change on screen shows it"
                + ("" if c.supported == "no" else " clearly")
                + ". Was it confirmed, and how?"
            ),
            severity=severity,
            window_time_range=win,
            step_id=step.step_id,
            status="unresolvable",
            finding_kind="unsupported_assertion",
        )

    assigned = {a.event_id: a.step_id for a in output.events if a.event_id in rec_by_id}
    assignment = {a.event_id: a for a in output.events if a.event_id in rec_by_id}
    shown_steps: List[SOPStep] = []
    taken_ids = set(steps_by_id)
    for r in sorted(records, key=lambda r: r.t_start):
        owner = assigned.get(r.event_id)
        if owner in steps_by_id or r.event_id in explained_events:
            continue
        shown = _shown_step(r, assignment.get(r.event_id), taken_ids)
        if shown is not None:
            taken_ids.add(shown.step_id)
            shown_steps.append(shown)
            continue
        prev_id, next_id = _neighbours(r.t_start, ordered, spans)
        pair = (prev_id, next_id) if prev_id and next_id else None
        if pair and any(f.between_step_ids == pair and f.family == "structural" for f in flags):
            continue
        add_flag(
            family="structural",
            question=(
                f"The screen changed at about {r.t_start:.0f}s ({r.type.replace('_', ' ')}) but no narrated "
                "step describes it. What action happened here?"
            ),
            severity="caution",
            window_time_range=(max(0.0, r.t_start - config.SCREEN_ALIGN_LEAD_S), r.t_end + config.SCREEN_ALIGN_LEAD_S),
            step_id=None if pair else (prev_id or next_id),
            between_step_ids=pair,
            event_ids=[r.event_id],
            finding_kind="observed_unexplained",
        )

    steps = _insert_by_time([new_steps[s.step_id] for s in doc.steps], shown_steps, spans)
    merged = doc.model_copy(update={"steps": steps, "flags": flags})
    for step_id, slot, fill in slot_fills:
        merged = fill_slot(merged, step_id, slot, fill)
    return add_slot_flags(merged, only_steps={s.step_id for s in shown_steps})
