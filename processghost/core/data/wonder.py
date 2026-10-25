"""Questions about the paths the recording did not show: one model call over a facts table, behind a guard (docs/26 flow plan).

Code supplies the evidence (core/data/context_facts.py: counts and ranges around the cells the recording touched) and
verifies what comes back; the one model call does the part code cannot, which is to notice that a recorded step is one
outcome of several and to phrase what the person should be asked. There is no rule per scenario here: nothing names a
column, a value, an approver or a threshold.

The model is never trusted about the data. A question survives only if
- it ends as a question and does not state a reason or cause (config.WONDER_STATES_REASON),
- it cites facts that exist,
- every number and every quoted value in its text occurs in the facts it cites,
- it is not already asked.
Anything else is dropped, counted and written to the trace, so the quality of the call can be measured rather than assumed.
Survivors become flags in the same shape as the repeat and parameter questions: for the person who recorded the process.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, NamedTuple, Optional, Tuple

from pydantic import BaseModel, Field

from core import config
from core.data.context_facts import ContextFact, FactsTable, build_context_facts
from core.data.guard import changed_cells, numbers_in
from core.data.tools import WorkbookTools
from core.llm import prompts
from core.models.data import RungAttempt, WorkbookProfile
from core.models.sop import Flag, Resolution, Severity, SOPDocument, SOPStep
from core.screen.cell_trail import TrailCell
from core.slots import step_window
from core.text_grounding import compact

_QUOTED = re.compile(r"(?<![A-Za-z0-9])'([^']{2,}?)'(?![A-Za-z0-9])|\"([^\"]{2,})\"")  # a possessive apostrophe (exception's) is not a quote


class WonderQuestion(BaseModel):
    observed: str = ""  # the contrast in the facts, written before the question (docs/16: observation before conclusion)
    question: str
    fact_ids: List[str] = Field(default_factory=list)
    step_id: Optional[str] = None
    severity: Severity = "caution"


class WonderOutput(BaseModel):
    questions: List[WonderQuestion] = Field(default_factory=list)


class Kept(NamedTuple):
    question: WonderQuestion
    cited: List[ContextFact]


def _user_text(doc: SOPDocument, table: FactsTable) -> str:
    payload = {
        "steps": [
            {"step_id": s.step_id, "title": s.title, "instruction": s.instruction, "kind": s.kind,
             "slots": {name: fill.value for name, fill in s.slots.items()}}
            for s in sorted(doc.steps, key=lambda s: s.order)
        ],
        "open_questions": [f.question for f in doc.flags],
        "facts": [{"id": f.fact_id, "text": f.text} for f in table.facts],
    }
    return json.dumps(payload, ensure_ascii=True, default=str)


def _violation(q: WonderQuestion, by_id: Dict[str, ContextFact]) -> Optional[str]:
    """Why a question cannot be kept, or None."""
    text = q.question.strip()
    if not text or not text.rstrip('"\' ').endswith("?"):
        return "not a question"
    lowered = text.lower()
    if any(w in lowered for w in config.WONDER_STATES_REASON):
        return "states a reason instead of asking"
    if not q.observed.strip():
        return "no observation written before the question"
    cited = [by_id[i] for i in q.fact_ids if i in by_id]
    if not cited:
        return "cites no fact in the table"
    evidence = " ".join(f.text for f in cited)
    known = numbers_in(evidence)
    folded = compact(evidence)
    for field in (q.observed, text):  # the observation is held to the same standard as the question
        for number in numbers_in(field):
            if not any(abs(number - k) < 1e-6 for k in known):
                return "the number %g is not in the facts it cites" % number
        for m in _QUOTED.finditer(field):
            value = m.group(1) or m.group(2)
            if compact(value) not in folded:
                return "the value '%s' is not in the facts it cites" % value
    return None


def ground_questions(output: WonderOutput, table: FactsTable, asked: set) -> Tuple[List[Kept], List[dict]]:
    by_id = {f.fact_id: f for f in table.facts}
    kept: List[Kept] = []
    dropped: List[dict] = []
    for q in output.questions:
        reason = _violation(q, by_id)
        if reason is None and compact(q.question) in asked:
            reason = "already asked"
        if reason is None and len(kept) >= config.WONDER_MAX_QUESTIONS:
            reason = "over the per-run question cap"
        if reason is not None:
            dropped.append({"question": q.question, "reason": reason})
            continue
        asked.add(compact(q.question))
        kept.append(Kept(q, [by_id[i] for i in q.fact_ids if i in by_id]))
    return kept, dropped


def _home_step(doc: SOPDocument, step_id: Optional[str]) -> Optional[SOPStep]:
    """The step a question attaches to: the one named, else the first data step the screen located."""
    named = next((s for s in doc.steps if s.step_id == step_id), None)
    return named or next((s for s in sorted(doc.steps, key=lambda s: s.order) if s.slots.get("target")), None)


def add_wonder_questions(client, doc: SOPDocument, trail: List[TrailCell], profile: WorkbookProfile, tools: WorkbookTools) -> SOPDocument:
    """One call. Non-fatal: any failure is recorded as `wonder_failed` and the document comes back unchanged."""
    changed = changed_cells(doc, trail)
    if not changed:
        return doc  # the recording changed nothing that was located: there is no step to wonder about
    try:
        table = build_context_facts(changed, trail, profile, tools)
        if not table.facts:
            return doc
        output: WonderOutput = client.structured_chat(
            prompts.WONDER_SYSTEM, _user_text(doc, table), WonderOutput, stage="wonder",
        )
        kept, dropped = ground_questions(output, table, {compact(f.question) for f in doc.flags})
        client.metrics.record_payload("wonder_guard", {"facts": len(table.facts), "facts_cut": table.dropped, "asked": len(output.questions), "kept": len(kept), "dropped": dropped})
        return _with_flags(doc, kept)
    except Exception as exc:  # noqa: BLE001 - an additive stage: its failure must not lose the run
        client.metrics.record_failure("wonder_failed", str(exc))
        return doc


def _with_flags(doc: SOPDocument, kept: List[Kept]) -> SOPDocument:
    flags, resolutions = list(doc.flags), list(doc.resolutions)
    for item in kept:
        step = _home_step(doc, item.question.step_id)
        seg_ids, window = step_window(step) if step else ([], None)
        if not seg_ids and window is None:
            continue
        cites, seen = [], set()
        for fact in item.cited:
            if (fact.cell.sheet, fact.cell.ref) not in seen:
                seen.add((fact.cell.sheet, fact.cell.ref))
                cites.append(fact.cell)
        flag_id = "FLAG-%02d" % (len(flags) + 1)
        reason = "found in the workbook: %s (%s)" % (item.question.observed.strip(), " ".join("[%s] %s" % (f.fact_id, f.text) for f in item.cited))
        flags.append(Flag(
            flag_id=flag_id, family="ambiguity", question=item.question.question.strip(), severity=item.question.severity,
            window_segment_ids=seg_ids, window_time_range=window, step_id=step.step_id if step else None,
            status="unresolvable", origin="data", finding_kind="unresolved_requirement",
            rungs=[RungAttempt(rung="document", outcome="candidates", reason=reason, citations=cites)],
        ))
        resolutions.append(Resolution(flag_id=flag_id, verdict="unresolvable", reason=reason + "; for the person who recorded the process"))
    return doc.model_copy(update={"flags": flags, "resolutions": resolutions})
