"""The cell trail (docs/26 flow plan): an accurate list of which cell was selected, where, when, and what it showed.

Built from the change records the surface stage already produces: the selected-cell reference read from the name
box (re-read with a changed input and, as a last resort, by the cheap vision model, always behind a cell-reference
validator, see read_fallback.py), the sheet position read from the status bar, and the formula-bar content. No model
call here. The trail is what lets the document rung look at the cells that were actually used instead of guessing,
and what lets a workbook answer be checked against what the screen showed.

The sheet is found by its position ("Sheet 2 of 3") first, because a name is easier for OCR to misread; a workbook
supplies the names for those positions in the same order.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel

from core.models.screen import ChangeRecord
from core.screen.read_fallback import valid_cell_ref
from core.text_grounding import compact


class TrailCell(BaseModel):
    event_id: str
    t_s: float
    sheet_index: Optional[int] = None  # 1-based position of the sheet, when legible
    sheet: str = ""  # its name, once a workbook is available to name the position
    cell: str
    content: str = ""  # what the formula bar showed for that cell
    read_by: str = "ocr"


def build_trail(records: List[ChangeRecord], sheet_names: Optional[List[str]] = None) -> List[TrailCell]:
    """One entry per event whose selected cell was read, in time order. `sheet_names` (the workbook's sheets in order)
    turns a legible sheet position into a name; with no workbook the name is matched from the locator text only."""
    names = sheet_names or []
    folded: Dict[str, str] = {compact(n): n for n in names}
    trail = []
    for r in sorted(records, key=lambda r: r.t_start):
        loc = r.surface_after
        if loc is None or not valid_cell_ref(loc.focus):
            continue
        sheet = ""
        if loc.index is not None and 1 <= loc.index <= len(names):
            sheet = names[loc.index - 1]
        elif loc.location and compact(loc.location) in folded:
            sheet = folded[compact(loc.location)]
        trail.append(TrailCell(
            event_id=r.event_id, t_s=r.t_start, sheet_index=loc.index, sheet=sheet, cell=loc.focus.strip().upper(),
            content=loc.focus_content or "", read_by=loc.read_by.get("focus", "ocr"),
        ))
    return trail
