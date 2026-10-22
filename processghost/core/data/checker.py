"""The document rung for ONE question (docs/26 flow plan): a short loop of structured model calls, each either a
read-only lookup or a verdict.

Why a loop of structured calls rather than native tool calling: every round goes through the same client the rest
of the pipeline uses (retry, cache, cost tracking, trace), works unchanged on every provider, and replays from the
cache. The price is that the loop is ours to bound, so it is bounded hard:

- at most DATA_MAX_CALLS_PER_FLAG lookups, then ONE more round that can only give a verdict;
- an identical repeated lookup is refused and counted as wasted;
- every lookup states the question it serves and why, and is written to the trace.

The model is never trusted about what the data says. A citation counts only if the (sheet, cell) was actually
returned by a lookup for THIS question, and its value and formula are copied from that result, not from the
model. A verdict of answered/contradicted with no surviving citation is downgraded: the answer then rests on
nothing a person can check.
"""
from __future__ import annotations

import json
from typing import Dict, List, Literal, NamedTuple, Optional, Tuple

from pydantic import BaseModel, Field

from core import config
from core.data.tools import Cell, Filter, ToolResult, WorkbookTools
from core.llm import prompts
from core.models.data import DataCitation, RungAttempt, WorkbookProfile
from core.models.sop import Flag, Severity, SOPDocument, SOPStep
from core.text_grounding import compact


class CallArgs(BaseModel):
    tool: Literal["read_rows", "query_rows"]
    sheet: str
    start: int = 1
    limit: int = 10
    filters: List[Filter] = Field(default_factory=list)
    columns: List[str] = Field(default_factory=list)


class CellRef(BaseModel):
    sheet: str
    ref: str


class NewQuestion(BaseModel):
    question: str
    severity: Severity = "caution"
    citations: List[CellRef] = Field(default_factory=list)


class DataCheckerStep(BaseModel):
    action: Literal["tool", "verdict"]
    reason: str = ""
    call: Optional[CallArgs] = None
    verdict: Optional[Literal["answered", "contradicted", "candidates", "no_answer"]] = None
    answer: str = ""
    citations: List[CellRef] = Field(default_factory=list)
    candidates: List[str] = Field(default_factory=list)
    new_questions: List[NewQuestion] = Field(default_factory=list)


class FoundQuestion(NamedTuple):
    question: str
    severity: str
    citations: List[DataCitation]


class DataOutcome(NamedTuple):
    attempt: RungAttempt
    new_questions: List[FoundQuestion]
    calls: int
    wasted: int


def _step_excerpt(step: Optional[SOPStep]) -> dict:
    if step is None:
        return {}
    return {
        "title": step.title, "instruction": step.instruction, "kind": step.kind,
        "slots": {name: fill.value for name, fill in step.slots.items()},
    }


def _established(flag: Flag) -> List[dict]:
    return [{"rung": r.rung, "outcome": r.outcome, "reason": r.reason, "answer": r.answer} for r in flag.rungs]


def _user_text(flag: Flag, step: Optional[SOPStep], profile: WorkbookProfile, history: List[dict], left: int,
               observed: Optional[List[dict]] = None) -> str:
    payload = {
        "question": flag.question, "family": flag.family, "slot": flag.slot, "severity": flag.severity,
        "status_so_far": flag.status, "step": _step_excerpt(step), "already_established": _established(flag),
        "profile": profile.model_dump(mode="json", exclude={"sha256"}), "lookups_so_far": history,
        "lookups_left": left,
        "observed_on_screen": observed or [],
    }
    if left <= 0:
        payload["instruction"] = "No lookups are left. Give your verdict now."
    return json.dumps(payload, ensure_ascii=True, default=str)


_FACT_SLOTS = ("key", "effect")  # answered from workbook facts, not from the cells that were selected


def _matches_screen(citations: List[DataCitation], observed: List[dict]) -> bool:
    """True if a cited cell is one the screen showed (same sheet and cell) or holds the same value as one it showed. An answer
    about the recorded instance that cites only other rows (another month, another bill) is not about what was recorded."""
    cells = {(o.get("sheet", ""), o.get("cell", "")) for o in observed}
    shown = {compact(o.get("content", "")) for o in observed if o.get("content")}
    return any((c.sheet, c.ref) in cells or (c.value and compact(str(c.value)) in shown) for c in citations)


def _run_tool(tools: WorkbookTools, call: CallArgs) -> ToolResult:
    if call.tool == "read_rows":
        return tools.read_rows(call.sheet, call.start, call.limit)
    return tools.query_rows(call.sheet, call.filters, call.columns or None, call.limit)


def _verified(refs: List[CellRef], returned: Dict[Tuple[str, str], Cell], workbook: str) -> List[DataCitation]:
    out, seen = [], set()
    for r in refs:
        key = (r.sheet.strip(), r.ref.strip().upper())
        cell = returned.get(key)
        if cell is None or key in seen:
            continue
        seen.add(key)
        out.append(DataCitation(workbook=workbook, sheet=key[0], ref=key[1], value=cell.value, formula=cell.formula))
    return out


def check_flag_with_data(
    client,
    flag: Flag,
    doc: SOPDocument,
    profile: WorkbookProfile,
    tools: WorkbookTools,
    *,
    max_calls: int = config.DATA_MAX_CALLS_PER_FLAG,
    observed: Optional[List[dict]] = None,
) -> DataOutcome:
    step = next((s for s in doc.steps if s.step_id == flag.step_id), None)
    returned: Dict[Tuple[str, str], Cell] = {}
    history: List[dict] = []
    seen_calls = set()
    calls = wasted = 0
    verdict: Optional[DataCheckerStep] = None

    for _ in range(max_calls + 1):
        out: DataCheckerStep = client.structured_chat(
            prompts.DATA_CHECKER_SYSTEM, _user_text(flag, step, profile, history, max_calls - calls, observed), DataCheckerStep,
            stage="document", payload_extra={"flag_id": flag.flag_id},
        )
        if out.action == "verdict":
            verdict = out
            break
        if calls >= max_calls or out.call is None:
            break  # asked for a lookup it has no budget for (or sent none): treated as no answer below
        key = json.dumps(out.call.model_dump(mode="json"), sort_keys=True)
        if key in seen_calls:
            result = ToolResult(ok=False, error="this exact lookup was already made; use its earlier result")
        else:
            seen_calls.add(key)
            result = _run_tool(tools, out.call)
        calls += 1
        is_wasted = not result.ok
        wasted += int(is_wasted)
        for row in result.rows:
            for cell in row.cells:
                returned[(result.sheet, cell.ref)] = cell
        history.append({"lookup": out.call.model_dump(mode="json"), "reason": out.reason, "result": result.model_dump(mode="json")})
        client.metrics.record_payload("document_call", {
            "flag_id": flag.flag_id, "reason": out.reason, "tool": out.call.tool, "sheet": out.call.sheet,
            "rows_returned": len(result.rows), "matched_count": result.matched_count, "truncated": result.truncated,
            "wasted": is_wasted, "error": result.error,
        })

    if verdict is None:
        return DataOutcome(
            RungAttempt(rung="document", outcome="no_answer", reason="no verdict within %d lookup(s)" % calls), [], calls, wasted
        )

    citations = _verified(verdict.citations, returned, tools.name)
    outcome, reason, candidates = verdict.verdict or "no_answer", verdict.reason, [c.strip() for c in verdict.candidates if c.strip()]
    if outcome in ("answered", "contradicted") and (not citations or not verdict.answer.strip()):
        outcome, reason = "no_answer", "the verdict cited no cell that a lookup returned, so it rests on nothing checkable"
        candidates = []
    if outcome == "candidates" and not candidates:
        outcome = "no_answer"
    if outcome in ("answered", "contradicted") and observed and flag.event_ids and flag.slot not in _FACT_SLOTS and not _matches_screen(citations, observed):
        outcome, reason, candidates = "no_answer", "the cited cells include none the screen showed, so the answer is not about what was recorded", []
    found = [
        FoundQuestion(q.question.strip(), q.severity, _verified(q.citations, returned, tools.name))
        for q in verdict.new_questions
        if q.question.strip() and _verified(q.citations, returned, tools.name)
    ]
    attempt = RungAttempt(
        rung="document", outcome=outcome, reason=reason, answer=verdict.answer.strip() if outcome in ("answered", "contradicted") else "",
        citations=citations if outcome != "no_answer" else [], candidates=candidates if outcome == "candidates" else [],
    )
    return DataOutcome(attempt, found, calls, wasted)
