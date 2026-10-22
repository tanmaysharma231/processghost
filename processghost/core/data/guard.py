"""Guard on the video Checker's claims, using the workbook (docs/26 flow plan).

The failure it prevents (seen in a real run): the Checker read a real number off a screenshot, the billed total, and wrote
that the pasted value "contributes to the total of" it. The number was on screen, so grounding it in the evidence cannot
catch it. The mistake is which cell the number belongs to.

The rule: a number in a Checker claim that equals the value of a formula cell must be the value of a cell that depends, directly
or through other formulas, on a cell this recording changed. A total over a different column is a different cell, however
close it sits on screen. A claim that breaks the rule is withdrawn: its flag goes back to unresolvable (a question for the
person), its inferred clause or inserted step is removed, and the reason names the cell the number really belongs to.

Numbers that do not equal any computable formula value are left alone: they may be plain values, and nothing here can tell.
This guard rejects wrong attributions; it cannot confirm a claim is true.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from core.data import facts
from core.data.tools import WorkbookTools
from core.models.data import RungAttempt
from core.models.sop import Resolution, SOPDocument
from core.screen.cell_trail import TrailCell

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_MIN_DIGITS = 3  # smaller numbers are counts and ordinals ("step 2"), not amounts


def numbers_in(text: str) -> List[float]:
    out = []
    for m in _NUMBER.finditer(text or ""):
        raw = m.group(0).rstrip(",")
        if sum(c.isdigit() for c in raw) >= _MIN_DIGITS:
            out.append(float(raw.replace(",", "")))
    return out


def changed_cells(doc: SOPDocument, trail: List[TrailCell]) -> List[Tuple[str, str]]:
    """The cells this recording changed: the target of each data step that the screen located."""
    by_event = {t.event_id: t for t in trail}
    cells = []
    for step in doc.steps:
        fill = step.slots.get("target")
        located = [by_event[e] for e in (fill.event_ids if fill else []) if e in by_event]
        if located and located[-1].sheet:
            cells.append((located[-1].sheet, located[-1].cell))
    return cells


def _violation(text: str, computed: Dict[Tuple[str, str], float], reach: Set[Tuple[str, str]]) -> Optional[str]:
    for number in numbers_in(text):
        sources = [key for key, value in computed.items() if abs(value - number) < 1e-6]
        if sources and not any(s in reach for s in sources):
            sheet, ref = sources[0]
            return "%s is the value of %s!%s, which does not depend on a cell this recording changed" % (("%g" % number), sheet, ref)
    return None


def guard_claims(doc: SOPDocument, trail: List[TrailCell], tools: WorkbookTools) -> SOPDocument:
    changed = changed_cells(doc, trail)
    if not changed:
        return doc  # nothing located as changed: no basis to say what a number should depend on
    deps = facts.parse_dependencies(tools)
    computed = facts.computed_values(tools)
    reach = facts.reachable_from(deps, changed)

    withdrawn: Dict[str, str] = {}
    for res in doc.resolutions:
        if res.verdict != "resolved" or not res.inferred_text:
            continue
        reason = _violation(res.inferred_text, computed, reach)
        if reason:
            withdrawn[res.flag_id] = reason
    if not withdrawn:
        return doc

    flags = []
    for f in doc.flags:
        if f.flag_id in withdrawn:
            video = RungAttempt(rung="video", outcome="no_answer", reason="withdrawn: " + withdrawn[f.flag_id], event_ids=list(f.event_ids))
            f = f.model_copy(update={"status": "unresolvable", "rungs": [video] + [r for r in f.rungs if r.rung != "video"]})
        flags.append(f)
    resolutions = [
        Resolution(flag_id=r.flag_id, verdict="unresolvable", reason="withdrawn: " + withdrawn[r.flag_id]) if r.flag_id in withdrawn else r
        for r in doc.resolutions
    ]
    inserted = {"step-ins-%s" % fid for fid in withdrawn}
    steps = []
    for s in doc.steps:
        if s.step_id in inserted:
            continue  # a step that existed only because a withdrawn claim inserted it
        steps.append(s.model_copy(update={"inferred_clauses": [c for c in s.inferred_clauses if c.flag_id not in withdrawn]}))
    steps = [s.model_copy(update={"order": i + 1}) for i, s in enumerate(sorted(steps, key=lambda s: s.order))]
    return doc.model_copy(update={"flags": flags, "resolutions": resolutions, "steps": steps})
