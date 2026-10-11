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
from core.models.reconcile import ReconcilerOutput
from core.models.screen import ChangeRecord
from core.models.sop import Flag, ObservedChange, SOPDocument, SOPStep
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


def build_reconciler_input(doc: SOPDocument, records: List[ChangeRecord], segments_raw: List[dict]) -> str:
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
            }
        )
    events = [
        {
            "event_id": r.event_id,
            "t_start": r.t_start,
            "t_end": r.t_end,
            "type": r.type,
            "chrome_before": r.chrome_before,
            "chrome_after": r.chrome_after,
            "regions": [{"before": g.before, "after": g.after} for g in r.regions],
        }
        for r in records
    ]
    return str({"steps": steps, "events": events})


def _window(step: SOPStep, spans: Dict[str, Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    return spans.get(step.step_id)


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
                        f"Step {step.order} ('{step.title}') was narrated but no change on screen matches it. "
                        f"Was it done off screen, or did it not take effect?{note}"
                    ),
                    severity="caution",
                    window_time_range=win,
                    step_id=step.step_id,
                    status="unresolvable",
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
        )

    assigned = {a.event_id: a.step_id for a in output.events if a.event_id in rec_by_id}
    for r in sorted(records, key=lambda r: r.t_start):
        owner = assigned.get(r.event_id)
        if owner in steps_by_id or r.event_id in explained_events:
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
        )

    return doc.model_copy(update={"steps": [new_steps[s.step_id] for s in doc.steps], "flags": flags})
