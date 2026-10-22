"""Which values are probably parameters, and what would break next time (docs/26 flow plan). Decided by the workbook, no model.

A recording is one instance of a process. The flow it yields is only reusable if the values that change from run to run are
named as parameters and the things that quietly assume this run's values are called out. The workbook shows both:

- PARAMETER: a value from the row that was changed that sits in a grouping column (few distinct values, repeating down the sheet:
  a month, a category) AND is typed into a formula as a literal criterion. A repeating column with no such tie (a category that
  merely repeats) is not reported. A sheet name that carries the value is reported too, but only alongside a typed-in value: a sheet
  name alone is often a coincidence of vocabulary.
- NOTE "hard-coded value": a formula that has this run's value typed into it. If the process is run for the next period, someone
  must change the formula, or the new rows are not counted.
- NOTE "value in a sheet name": the sheet carries this run's value in its name (the real name is kept). Next period there will
  be another sheet, or this one is replaced.
- NOTE "result mechanism": for a step that is a result (something that happens by itself), the formulas that read the changed
  cell are what make it happen.

Each parameter also becomes a question for the person: does it change each time, and how is it chosen? Nothing here asserts the
process is run periodically; it points out what would matter if it is.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries
from openpyxl.utils import get_column_letter

from core import config
from core.data import facts
from core.data.guard import changed_cells
from core.data.tools import WorkbookTools
from core.models.data import DataCitation, FlowNote, Parameter, RungAttempt, WorkbookProfile
from core.models.sop import Flag, Resolution, SOPDocument
from core.slots import step_window
from core.text_grounding import compact

_WORD = re.compile(r"[A-Za-z]{3,}")
_MAX_DISTINCT_SHOWN = 6


def _carries(sheet_name: str, value: str) -> bool:
    """True if a word of `value` and a word of the sheet name share a prefix of at least three letters ("Mar" and "March")."""
    for w in (x.lower() for x in _WORD.findall(value)):
        for s in (x.lower() for x in _WORD.findall(sheet_name)):
            if w.startswith(s) or s.startswith(w):
                return True
    return False


def _cite(tools: WorkbookTools, sheet: str, ref: str):
    cell = tools.cell_at(sheet, ref)
    if cell is None or (cell.value is None and cell.formula is None):
        return None
    return DataCitation(workbook=tools.name, sheet=sheet, ref=ref, value=cell.value, formula=cell.formula)


def find_parameters(doc: SOPDocument, trail, profile: WorkbookProfile, tools: WorkbookTools) -> Tuple[List[Parameter], List[FlowNote]]:
    params: List[Parameter] = []
    notes: List[FlowNote] = []
    deps = facts.parse_dependencies(tools)
    seen = set()
    for sheet, ref in dict.fromkeys(changed_cells(doc, trail)):
        row, _col = coordinate_to_tuple(ref)
        sheet_profile = next((s for s in profile.sheets if s.name == sheet), None)
        if sheet_profile is None:
            continue
        for column in sheet_profile.columns:
            if column.kind != "text" or not (2 <= column.distinct_count <= config.DATA_DISTINCT_MAX) or column.distinct_count >= column.non_empty:
                continue
            cell = tools.cell_at(sheet, "%s%d" % (column.letter, row))
            value = cell.value if cell is not None else None
            if not value or (sheet, column.letter) in seen:
                continue
            evidence: List[str] = []
            found: List[FlowNote] = []
            for d in deps:
                for cond in d.conditions:
                    min_col, _r1, max_col, _r2 = range_boundaries(cond.range)
                    if cond.sheet == sheet and min_col == max_col and get_column_letter(min_col) == column.letter and facts.norm(cond.criterion) == facts.norm(value):
                        text = "%s!%s has '%s' typed into it (a criterion on %s)." % (d.sheet, d.ref, cond.criterion, cond.range)
                        evidence.append(text)
                        found.append(FlowNote(
                            kind="hardcoded_value", text=text + " If this process is run for another %s, the formula must be changed or the new rows are not counted." % (column.header or "value"),
                            citations=[c for c in (_cite(tools, d.sheet, d.ref),) if c],
                        ))
            for name in tools.sheetnames:
                if _carries(name, value):
                    text = "The sheet '%s' carries '%s' in its name." % (name, value)
                    evidence.append(text)
                    found.append(FlowNote(
                        kind="value_in_sheet_name",
                        text=text + " If this process is run for another %s, there will be a different sheet or this one is replaced." % (column.header or "value"),
                    ))
            if not evidence:
                continue
            if not any(n.kind == "hardcoded_value" for n in found):
                continue  # a sheet name alone is a coincidence of vocabulary ("Bills" and BILL-001); a typed-in value is evidence
            seen.add((sheet, column.letter))
            shown = ", ".join(column.distinct[:_MAX_DISTINCT_SHOWN])
            params.append(Parameter(
                name=column.header or column.letter, sheet=sheet, column=column.letter, example=value,
                evidence=["%s!%s holds %d distinct values over %d rows (%s)." % (sheet, column.letter, column.distinct_count, column.non_empty, shown)] + evidence,
            ))
            notes.extend(found)
    return params, notes


def result_notes(doc: SOPDocument, trail, tools: WorkbookTools) -> List[FlowNote]:
    """For each step that is a result, the formulas that read the cell the recording changed: what makes it happen."""
    changed = changed_cells(doc, trail)
    if not changed:
        return []
    deps = facts.parse_dependencies(tools)
    readers = [d for sheet, ref in changed for d in facts.dependents(deps, sheet, ref)]
    if not readers:
        return []
    text = "It is produced by: " + " | ".join("%s!%s (%s)" % (d.sheet, d.ref, d.formula) for d in readers) + "."
    cites = [c for c in (_cite(tools, d.sheet, d.ref) for d in readers) if c]
    return [FlowNote(kind="result_mechanism", text=text, step_id=s.step_id, citations=cites) for s in doc.steps if s.kind == "result"]


def add_parameters(doc: SOPDocument, trail, profile: WorkbookProfile, tools: WorkbookTools) -> SOPDocument:
    params, notes = find_parameters(doc, trail, profile, tools)
    notes = notes + result_notes(doc, trail, tools)
    if not params and not notes:
        return doc
    flags, resolutions = list(doc.flags), list(doc.resolutions)
    asked = {compact(f.question) for f in flags}
    step = next((s for s in doc.steps if s.slots.get("target")), None)
    seg_ids, window = step_window(step) if step else ([], None)
    for p in params:
        question = "Does the %s change each time this process is run, and how is it chosen? This recording used '%s'." % (p.name, p.example)
        if compact(question) in asked or (not seg_ids and window is None):
            continue
        asked.add(compact(question))
        flag_id = "FLAG-%02d" % (len(flags) + 1)
        reason = "found in the workbook: " + p.evidence[-1]
        flags.append(Flag(
            flag_id=flag_id, family="ambiguity", question=question, severity="caution", window_segment_ids=seg_ids, window_time_range=window,
            step_id=step.step_id if step else None, status="unresolvable", origin="data", finding_kind="unresolved_requirement",
            rungs=[RungAttempt(rung="document", outcome="candidates", reason=reason)],
        ))
        resolutions.append(Resolution(flag_id=flag_id, verdict="unresolvable", reason=reason + "; for the person who recorded the process"))
    return doc.model_copy(update={"parameters": params, "flow_notes": list(doc.flow_notes) + notes, "flags": flags, "resolutions": resolutions})
