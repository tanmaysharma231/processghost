"""Merge a document-rung outcome into the SOPDocument (docs/26 flow plan). Pure functions, no model.

What an outcome may change, and what it may not:

- answered: an unresolved question becomes resolved, citing the cells (a slot question fills the slot). A question the
  video already resolved is left as it is: the document agrees, which the recorded rung shows.
- contradicted: the question becomes `contradicted` WHATEVER it was, including one the video resolved. The screen
  can show one value while the real rule differs, and that must reach the person, never be settled by the pipeline.
- candidates / no_answer: an open question becomes unresolvable (every automated source has now been tried);
  nothing already resolved is touched.
- New questions the data revealed become flags of their own (origin "data"), left unresolved for the person: the
  call that found them never answers them.

The base document's text is never edited; the answer is attached next to it (resolutions, slots, rungs).
"""
from __future__ import annotations

from typing import List

from core.data.checker import DataOutcome, FoundQuestion
from core.models.data import DataCitation, RungAttempt
from core.models.sop import Flag, Resolution, SlotFill, SOPDocument
from core.slots import OPEN_STATUSES, fill_slot
from core.text_grounding import compact


def _replace_flag(doc: SOPDocument, flag_id: str, **update) -> SOPDocument:
    return doc.model_copy(update={"flags": [f.model_copy(update=update) if f.flag_id == flag_id else f for f in doc.flags]})


def _set_resolution(doc: SOPDocument, res: Resolution) -> SOPDocument:
    kept = [r for r in doc.resolutions if r.flag_id != res.flag_id]
    return doc.model_copy(update={"resolutions": kept + [res]})


def _flag(doc: SOPDocument, flag_id: str) -> Flag:
    return next(f for f in doc.flags if f.flag_id == flag_id)


def skipped_attempt(reason: str) -> RungAttempt:
    return RungAttempt(rung="document", outcome="skipped", reason=reason)


def attach_attempt(doc: SOPDocument, flag_id: str, attempt: RungAttempt) -> SOPDocument:
    flag = _flag(doc, flag_id)
    return _replace_flag(doc, flag_id, rungs=list(flag.rungs) + [attempt])


def _resolve(doc: SOPDocument, flag: Flag, attempt: RungAttempt) -> SOPDocument:
    if flag.slot and flag.step_id:
        doc = fill_slot(doc, flag.step_id, flag.slot, SlotFill(value=attempt.answer, by="document", citations=list(attempt.citations)))
        if _flag(doc, flag.flag_id).status == "resolved":
            return doc
    doc = _replace_flag(doc, flag.flag_id, status="resolved")
    return _set_resolution(doc, Resolution(
        flag_id=flag.flag_id, verdict="resolved", inferred_text=attempt.answer, citations=list(attempt.citations),
        reason="answered from the workbook",
    ))


def _contradict(doc: SOPDocument, flag: Flag, attempt: RungAttempt) -> SOPDocument:
    previous = next((r for r in doc.resolutions if r.flag_id == flag.flag_id), None)
    said = (previous.inferred_text if previous and previous.inferred_text else "") or flag.question
    doc = _replace_flag(doc, flag.flag_id, status="contradicted", severity="blocking")
    return _set_resolution(doc, Resolution(
        flag_id=flag.flag_id, verdict="contradicted", narration_version=said, screen_version=attempt.answer,
        citations=list(attempt.citations), reason="the workbook disagrees with what was said or shown",
    ))


def _add_found(doc: SOPDocument, source: Flag, found: List[FoundQuestion]) -> SOPDocument:
    asked = {compact(f.question) for f in doc.flags}
    for q in found:
        if compact(q.question) in asked:
            continue
        asked.add(compact(q.question))
        new_id = "FLAG-%02d" % (len(doc.flags) + 1)
        flag = Flag(
            flag_id=new_id, family="ambiguity", question=q.question, severity=q.severity,
            window_segment_ids=list(source.window_segment_ids), window_time_range=source.window_time_range,
            step_id=source.step_id, status="unresolvable", origin="data", finding_kind="potential_conflict",
            rungs=[RungAttempt(rung="document", outcome="candidates", reason="found while checking %s" % source.flag_id, citations=list(q.citations))],
        )
        doc = doc.model_copy(update={"flags": list(doc.flags) + [flag]})
        doc = _set_resolution(doc, Resolution(
            flag_id=new_id, verdict="unresolvable", citations=list(q.citations),
            reason="raised from the workbook while checking %s; for the person who recorded the process" % source.flag_id,
        ))
    return doc


def apply_outcome(doc: SOPDocument, flag_id: str, outcome: DataOutcome) -> SOPDocument:
    flag = _flag(doc, flag_id)
    attempt = outcome.attempt
    doc = attach_attempt(doc, flag_id, attempt)
    flag = _flag(doc, flag_id)
    if attempt.outcome == "answered" and flag.status in OPEN_STATUSES:
        doc = _resolve(doc, flag, attempt)
    elif attempt.outcome == "contradicted" and (flag.status in OPEN_STATUSES or flag.status == "resolved"):
        doc = _contradict(doc, flag, attempt)
    elif attempt.outcome in ("candidates", "no_answer") and flag.status == "open":
        doc = _replace_flag(doc, flag_id, status="unresolvable")
        reason = attempt.reason or "the video and the workbook were both checked and neither settles it"
        if attempt.candidates:
            reason = "the workbook narrows it to: %s" % "; ".join(attempt.candidates)
        doc = _set_resolution(doc, Resolution(flag_id=flag_id, verdict="unresolvable", reason=reason, citations=list(attempt.citations)))
    return _add_found(doc, _flag(doc, flag_id), outcome.new_questions)
