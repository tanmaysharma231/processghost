"""The four questions every data step must answer (docs/26 flow plan).

A step that moves or changes data is complete only if it states

    source   where the value comes from
    key      how the right row or item is picked
    target   where exactly the value goes
    effect   what else reads the value that was changed (formulas, totals, links)

Which of them apply depends on the step kind. An applicable slot with no answer is a gap, and becomes a flag
deterministically (by omission, like a missing transition), never by the model noticing. Everything here is
generic: no sheet, column or value is named.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from core import config
from core.models.sop import Flag, Resolution, SlotFill, SOPDocument, SOPStep

APPLICABLE: Dict[str, Tuple[str, ...]] = {
    "move_data": ("source", "key", "target", "effect"),
    "edit": ("key", "target", "effect"),
}

# A missing key or target stops a builder cold; a missing source or effect is a gap that can be worked around.
SEVERITY: Dict[str, str] = {"source": "caution", "key": "blocking", "target": "blocking", "effect": "caution"}

_QUESTIONS: Dict[str, str] = {
    "source": "Where does the value in the step '%s' come from?",
    "key": "How is the right row or item picked in the step '%s'?",
    "target": "Where exactly does the value go in the step '%s'?",
    "effect": "What else uses the value changed in the step '%s' (formulas, totals, links)?",
}

OPEN_STATUSES = ("open", "unresolvable", "unchecked", "check_failed")


def applicable_slots(kind: Optional[str]) -> Tuple[str, ...]:
    return APPLICABLE.get(kind or "", ())


def open_slots(step: SOPStep) -> List[str]:
    return [s for s in applicable_slots(step.kind) if s not in step.slots]


def slot_question(slot: str, step: SOPStep) -> str:
    return _QUESTIONS[slot] % step.title


def step_window(step: SOPStep) -> Tuple[List[int], Optional[Tuple[float, float]]]:
    seg_ids = sorted({i for a in step.attributions if a.kind == "said" for i in a.segment_ids})
    if seg_ids:
        return seg_ids, None
    times = [a.t_s for a in step.attributions]
    t = min(times) if times else 0.0
    return [], (t, t + config.SLOT_SHOWN_WINDOW_S)


def make_slot_flag(flag_num: int, step: SOPStep, slot: str) -> Flag:
    seg_ids, window = step_window(step)
    return Flag(
        flag_id="FLAG-%02d" % flag_num, family="ambiguity", question=slot_question(slot, step),
        severity=SEVERITY[slot], window_segment_ids=seg_ids, window_time_range=window, step_id=step.step_id,
        slot=slot, origin="coverage",
    )


def add_slot_flags(doc: SOPDocument, only_steps: Optional[set] = None) -> SOPDocument:
    """One flag per applicable, unanswered slot that no existing flag already asks. `only_steps` limits the check
    to steps (by id) that are new since the last call, so a repeated call never duplicates a question."""
    flags = list(doc.flags)
    asked = {(f.step_id, f.slot) for f in flags if f.slot}
    for step in sorted(doc.steps, key=lambda s: s.order):
        if only_steps is not None and step.step_id not in only_steps:
            continue
        for slot in open_slots(step):
            if (step.step_id, slot) not in asked:
                flags.append(make_slot_flag(len(flags) + 1, step, slot))
                asked.add((step.step_id, slot))
    return doc.model_copy(update={"flags": flags})


def fill_slot(doc: SOPDocument, step_id: str, slot: str, fill: SlotFill) -> SOPDocument:
    """Record the answer on the step (only if the slot is still open) and resolve the flags that asked it,
    citing the screen events or cells that answered. Never overwrites an existing answer."""
    steps = []
    changed = False
    for s in doc.steps:
        if s.step_id == step_id and slot in applicable_slots(s.kind) and slot not in s.slots:
            s = s.model_copy(update={"slots": {**s.slots, slot: fill}})
            changed = True
        steps.append(s)
    if not changed:
        return doc
    flags, resolutions = [], list(doc.resolutions)
    have = {r.flag_id for r in resolutions}
    for f in doc.flags:
        if f.step_id == step_id and f.slot == slot and f.status in OPEN_STATUSES:
            f = f.model_copy(update={"status": "resolved"})
            if f.flag_id not in have:
                resolutions.append(
                    Resolution(
                        flag_id=f.flag_id, verdict="resolved", inferred_text=fill.value,
                        event_ids=list(fill.event_ids), citations=list(fill.citations),
                        reason="answered by %s" % fill.by,
                    )
                )
        flags.append(f)
    return doc.model_copy(update={"steps": steps, "flags": flags, "resolutions": resolutions})
