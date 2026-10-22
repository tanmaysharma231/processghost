"""Answer slot questions from the cell trail and the workbook facts, with no model (docs/26 flow plan).

Two of a data step's four questions are lookups once we know which cells were used:

- EFFECT, "what else reads the cell that was changed": the formulas that read it (core/data/facts.py), with each
  conditional criterion checked against the real cells. A criterion no cell satisfies is a finding in its own right: the
  total only counts rows that something never produced. It is raised as a new question for the person, not answered.
- KEY, "how is the right row picked": the columns on the sheet the value was taken from that hold the same text value
  as a cell in the row that was changed, restricted to columns whose values are all different there. One candidate is an
  answer; several are a confirmation for the person, with how well each identifies rows on the sheet being filled.

A question with no trail cell to start from is left for the model loop; nothing here guesses. Every citation is read
from the workbook by construction.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from openpyxl.utils.cell import coordinate_to_tuple

from core.data import facts
from core.data.checker import DataOutcome, FoundQuestion
from core.data.guard import changed_cells
from core.data.tools import WorkbookTools
from core.models.data import DataCitation, RungAttempt, WorkbookProfile
from core.models.sop import Flag, SOPDocument, SOPStep
from core.screen.cell_trail import TrailCell
from core.slots import OPEN_STATUSES


def _cite(tools: WorkbookTools, sheet: str, ref: str) -> Optional[DataCitation]:
    cell = tools.cell_at(sheet, ref)
    if cell is None or (cell.value is None and cell.formula is None):
        return None
    return DataCitation(workbook=tools.name, sheet=sheet, ref=ref, value=cell.value, formula=cell.formula)


def _trail_cell(step: SOPStep, slot: str, trail: Dict[str, TrailCell], last: bool = False) -> Optional[TrailCell]:
    fill = step.slots.get(slot)
    if fill is None:
        return None
    ids = [e for e in fill.event_ids if e in trail]
    if not ids:
        return None
    return trail[ids[-1] if last else ids[0]]


def _effect(step: SOPStep, flag: Flag, trail: Dict[str, TrailCell], deps, tools: WorkbookTools, fallback: Optional[TrailCell] = None) -> Optional[DataOutcome]:
    target = _trail_cell(step, "target", trail, last=True) or fallback
    if target is None or not target.sheet:
        return None
    readers = facts.dependents(deps, target.sheet, target.cell)
    where = "%s!%s" % (target.sheet, target.cell)
    if not readers:
        attempt = RungAttempt(rung="document", outcome="answered", answer="No formula in the workbook reads %s." % where,
                              citations=[c for c in [_cite(tools, target.sheet, target.cell)] if c])
        return DataOutcome(attempt, [], 0, 0)

    parts, citations, found = [], [], []
    for d in readers:
        cite = _cite(tools, d.sheet, d.ref)
        if cite:
            citations.append(cite)
        text = "%s!%s (%s)" % (d.sheet, d.ref, d.formula)
        for cond in (facts.evaluate(tools, c) for c in d.conditions):
            text += "; counts a row only where %s = '%s' (%d of %d cells in %s)" % (cond.range, cond.criterion, cond.matching, cond.total, cond.range)
            if cond.total and cond.matching == 0:
                first = _cite(tools, cond.sheet, next(facts.cells_in(cond.range)))
                found.append(FoundQuestion(
                    "%s!%s counts a row only where %s equals '%s', but none of the %d cells in %s has that value, and nothing shown "
                    "sets it. Is setting it a step of this process?" % (d.sheet, d.ref, cond.range, cond.criterion, cond.total, cond.range),
                    "blocking", citations[-1:] + ([first] if first else []),
                ))
        parts.append(text)
    attempt = RungAttempt(rung="document", outcome="answered", citations=citations,
                          answer="%s is read by: %s." % (where, " | ".join(parts)))
    return DataOutcome(attempt, found, 0, 0)


def _key(step: SOPStep, flag: Flag, trail: Dict[str, TrailCell], profile: WorkbookProfile, tools: WorkbookTools, fallback: Optional[TrailCell] = None) -> Optional[DataOutcome]:
    target, source = _trail_cell(step, "target", trail, last=True) or fallback, _trail_cell(step, "source", trail)
    if source is None and target is not None:
        # No confirmed source: the last cell selected on a different sheet before the target is where the value came from.
        earlier = [t for t in trail.values() if t.t_s < target.t_s and t.sheet and t.sheet != target.sheet]
        source = max(earlier, key=lambda t: t.t_s) if earlier else None
    if target is None or source is None or not target.sheet or not source.sheet or target.sheet == source.sheet:
        return None
    row, _col = coordinate_to_tuple(target.cell)
    candidates = facts.join_candidates(tools, profile, target.sheet, row, source.sheet)
    if not candidates:
        return DataOutcome(RungAttempt(
            rung="document", outcome="no_answer",
            reason="no text value in row %d of %s also appears in a unique column of %s" % (row, target.sheet, source.sheet)), [], 0, 0)

    def describe(j: facts.JoinCandidate) -> str:
        return "%s (%s column %s, %d distinct values in %d rows) matches %s (%s column %s)" % (
            j.target_header, target.sheet, j.target_column, j.target_distinct, j.target_rows, j.source_header, source.sheet, j.source_column)

    citations = []
    for j in candidates:
        citations += [c for c in (_cite(tools, target.sheet, j.target_ref), _cite(tools, source.sheet, j.source_ref)) if c]
    if len(candidates) == 1:
        attempt = RungAttempt(rung="document", outcome="answered", answer="Rows are matched on: " + describe(candidates[0]) + ".", citations=citations)
    else:
        attempt = RungAttempt(rung="document", outcome="candidates", citations=citations, candidates=[describe(j) for j in candidates],
                              reason="more than one column on %s carries the same value as a cell in that row" % source.sheet)
    return DataOutcome(attempt, [], 0, 0)


def deterministic_outcomes(doc: SOPDocument, trail: List[TrailCell], profile: WorkbookProfile, tools: WorkbookTools) -> Dict[str, DataOutcome]:
    """flag_id -> outcome for every effect and key question the trail and the facts can answer."""
    by_event = {t.event_id: t for t in trail}
    deps = facts.parse_dependencies(tools)
    steps = {s.step_id: s for s in doc.steps}
    # A step the screen could not locate a target for (its action was narrated but never shown in a cell) is about the same
    # recording: when the whole recording changed exactly one cell, that is the cell its questions are about.
    changed = set(changed_cells(doc, trail))
    fallback = next((t for t in reversed(trail) if (t.sheet, t.cell) in changed), None) if len(changed) == 1 else None
    out: Dict[str, DataOutcome] = {}
    for flag in doc.flags:
        step = steps.get(flag.step_id or "")
        if step is None or flag.slot not in ("effect", "key") or flag.status not in OPEN_STATUSES:
            continue
        result = _effect(step, flag, by_event, deps, tools, fallback) if flag.slot == "effect" else _key(step, flag, by_event, profile, tools, fallback)
        if result is not None:
            out[flag.flag_id] = result
    return out


def enrich_slots(doc: SOPDocument, trail: List[TrailCell], tools: WorkbookTools) -> SOPDocument:
    """Make a source or target answer that came from the screen precise: the sheet, the cell, the column header and the
    value the workbook holds there, with the cell cited. The screen still supplied the cell; the workbook names it."""
    by_event = {t.event_id: t for t in trail}
    steps = []
    for step in doc.steps:
        slots = dict(step.slots)
        for name in ("source", "target"):
            fill = slots.get(name)
            cell = _trail_cell(step, name, by_event, last=(name == "target"))
            if fill is None or fill.by != "shown" or cell is None or not cell.sheet:
                continue
            read = tools.cell_at(cell.sheet, cell.cell)
            if read is None:
                continue
            header = " (%s)" % read.header if read.header else ""
            cite = _cite(tools, cell.sheet, cell.cell)
            slots[name] = fill.model_copy(update={
                "value": "%s!%s%s" % (cell.sheet, cell.cell, header) + (" value %s" % read.value if read.value else ""),
                "citations": [cite] if cite else [],
            })
        steps.append(step.model_copy(update={"slots": slots}))
    return doc.model_copy(update={"steps": steps})
