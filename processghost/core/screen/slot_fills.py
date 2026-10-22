"""Fill a step's slots from confirmed selection anchors (docs/26 flow plan), with no model.

A confirmed source anchor means: a cell was selected, its value was mentioned (or pointed at) in speech, and
the same value then appeared in a later data change. That is the whole copy-and-paste story, observed twice
independently, so it answers two of a data step's questions deterministically:

    source   the cell that was selected, with its value
    target   the cell the value landed in

and shows the step moved a value, so a step with no better classification becomes "move_data". Unconfirmed
anchors are only candidates and fill nothing.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from core.models.screen import ChangeRecord, SelectionAnchor
from core.models.sop import SlotFill, SOPDocument, SOPStep
from core.slots import add_slot_flags, fill_slot


def _step_for_event(doc: SOPDocument, event_id: str) -> Optional[SOPStep]:
    for step in doc.steps:
        if any(event_id in oc.event_ids for oc in step.observed_changes):
            return step
        if any(event_id in a.event_ids for a in step.attributions if a.kind == "shown"):
            return step
    return None


def fill_slots_from_anchors(doc: SOPDocument, anchors: List[SelectionAnchor], records: List[ChangeRecord]) -> SOPDocument:
    by_event: Dict[str, ChangeRecord] = {r.event_id: r for r in records}
    touched = set()
    for a in anchors:
        if not (a.confirmed and a.role == "source" and a.target_event_id):
            continue
        step = _step_for_event(doc, a.target_event_id)
        if step is None:
            continue
        if step.kind in (None, "edit", "other"):
            doc = doc.model_copy(update={
                "steps": [s.model_copy(update={"kind": "move_data"}) if s.step_id == step.step_id else s for s in doc.steps]
            })
        value = a.focus + (" (value %s)" % a.content if a.content else "")
        doc = fill_slot(doc, step.step_id, "source", SlotFill(value=value, by="shown", event_ids=[a.event_id, a.target_event_id]))
        landed = by_event.get(a.target_event_id)
        focus = landed.surface_after.focus if landed and landed.surface_after else ""
        if focus:
            doc = fill_slot(doc, step.step_id, "target", SlotFill(value=focus, by="shown", event_ids=[a.target_event_id]))
        touched.add(step.step_id)
    return add_slot_flags(doc, only_steps=touched) if touched else doc
