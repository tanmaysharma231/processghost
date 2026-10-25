"""A bounded table of facts about the cells the recording touched (docs/26 flow plan). Code only, no model, no judgment.

The recording shows ONE instance of a process. The workbook shows what the rest of the sheet looks like around it. This
module writes down, for the cells the recording changed or selected, what the file holds in the same column and in the
other columns of the same rows, so that a single later model call can notice "this is one of several outcomes" and ask
what decides between them (core/data/wonder.py).

It never says what anything means: no column is called a status, an approver or a threshold. It states counts and
ranges, each as one short numbered fact (F1, F2, ...) tied to the cell it is about. The caps (config.WONDER_MAX_*) keep it
small: it never lists rows, other rows' ids or free text, only values that repeat and their counts.

A formula column of the form `=X{r}-Y{r}` or `=X{r}+Y{r}` over two cells of its own row is computed here from the file's
values, because a workbook written by a script has no stored results; anything more complex is reported as formula text
only, never guessed.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_to_tuple
from pydantic import BaseModel, Field

from core import config
from core.data import facts
from core.data.tools import WorkbookTools
from core.models.data import ColumnProfile, DataCitation, SheetProfile, WorkbookProfile
from core.screen.cell_trail import TrailCell
from core.text_grounding import to_number

_BLANK = "(blank)"
_PAIR_FORMULA = re.compile(r"^=\s*\$?([A-Z]{1,3})\{r\}\s*([-+])\s*\$?([A-Z]{1,3})\{r\}\s*$")


class ContextFact(BaseModel):
    fact_id: str
    text: str
    cell: DataCitation  # the cell the fact is about


class FactsTable(BaseModel):
    facts: List[ContextFact] = Field(default_factory=list)
    dropped: int = 0  # facts built but cut by WONDER_MAX_FACTS


def _fmt(number: float) -> str:
    return "%d" % number if float(number).is_integer() else "%g" % number


class _Sheet:
    """The rows of one sheet as text, with computed values for simple row formulas."""

    def __init__(self, tools: WorkbookTools, profile: SheetProfile):
        self.profile = profile
        self.rows: Dict[int, Dict[int, Optional[str]]] = {}
        first = (profile.header_row or 0) + 1
        for row, col, cell in tools.sheet_cells(profile.name):
            if row >= first:
                self.rows.setdefault(row, {})[col] = cell.value
        self.derived: Dict[str, Tuple[str, str, str]] = {}
        for fc in profile.formula_columns:
            m = _PAIR_FORMULA.match(fc.pattern)
            if m:
                self.derived[fc.column] = (m.group(1), m.group(2), m.group(3))

    def column(self, letter: str) -> Dict[int, Optional[str]]:
        """row -> shown text, or None for a blank cell; a simple row formula is computed from its two operands."""
        if letter in self.derived:
            left, op, right = self.derived[letter]
            out: Dict[int, Optional[str]] = {}
            for row in self.rows:
                a, b = to_number(self._text(row, left)), to_number(self._text(row, right))
                out[row] = None if a is None or b is None else _fmt(a - b if op == "-" else a + b)
            return out
        index = column_index_from_string(letter)
        return {row: cells.get(index) for row, cells in self.rows.items()}

    def _text(self, row: int, letter: str) -> Optional[str]:
        return self.rows.get(row, {}).get(column_index_from_string(letter))


def _kind(values: List[Optional[str]]) -> str:
    """"text" for a column of few repeating values, "number", "unique" for an identifier-like column, else "other"."""
    shown = [v for v in values if v not in (None, "")]
    if len(shown) < 2:
        return "other"
    distinct = set(shown)
    if len(distinct) < 2:
        return "other"  # a constant column says nothing about outcomes
    if all(to_number(v) is not None for v in shown):
        return "number"
    if len(distinct) == len(shown):
        return "unique"
    return "text" if 2 <= len(distinct) <= config.DATA_DISTINCT_MAX else "other"


def _counts_text(values: List[Optional[str]]) -> str:
    counts = Counter(v if v not in (None, "") else _BLANK for v in values)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    shown = ["%s (%d)" % (k, n) for k, n in ordered[: config.WONDER_MAX_VALUES_SHOWN]]
    more = len(ordered) - len(shown)
    return ", ".join(shown) + (" and %d more" % more if more > 0 else "")


def _range_text(values: List[Optional[str]]) -> str:
    numbers = [n for n in (to_number(v) for v in values if v not in (None, "")) if n is not None]
    return "no numbers" if not numbers else "%s to %s (%d row%s)" % (_fmt(min(numbers)), _fmt(max(numbers)), len(numbers), "" if len(numbers) == 1 else "s")


def _column_fact(sheet: _Sheet, column: ColumnProfile) -> str:
    values = list(sheet.column(column.letter).values())
    kind = _kind(values)
    head = "Column %s '%s' on %s" % (column.letter, column.header or column.letter, sheet.profile.name)
    if column.letter in sheet.derived:
        left, op, right = sheet.derived[column.letter]
        head += " is a formula, %s%s%s on each row, computed from the file" % (left, op, right)
    shown = sum(1 for v in values if v not in (None, ""))
    if kind == "text":
        text = "%s: over %d rows it holds: %s." % (head, len(values), _counts_text(values))
        unused = [a for a in column.allowed if a not in {v for v in values if v}]
        allowed = " Its dropdown allows: %s." % ", ".join(column.allowed) if column.allowed else ""
        return text + allowed + (" Never used in the rows: %s." % ", ".join(unused) if unused else "")
    if kind == "number":
        return "%s: values range %s." % (head, _range_text(values))
    if kind == "unique":
        return "%s: all %d values are different (identifier-like)." % (head, shown)
    return "%s: %d rows have a value." % (head, shown)


def _crosstab_facts(sheet: _Sheet, changed: ColumnProfile) -> List[str]:
    """For a changed column that holds few repeating values: what each other column of the same rows holds, grouped by it."""
    groups: Dict[str, List[int]] = {}
    for row, value in sheet.column(changed.letter).items():
        if value not in (None, ""):
            groups.setdefault(value, []).append(row)
    if len(groups) < 2:
        return []
    out: List[str] = []
    for other in sheet.profile.columns:
        if other.letter == changed.letter or len(out) >= config.WONDER_MAX_COMPANIONS:
            continue
        values = sheet.column(other.letter)
        kind = _kind(list(values.values()))
        if kind not in ("text", "number"):
            continue
        parts = []
        for group, rows in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))[: config.WONDER_MAX_VALUES_SHOWN]:
            column_values = [values.get(r) for r in rows]
            parts.append("%s: %s" % (group, _counts_text(column_values) if kind == "text" else _range_text(column_values)))
        out.append("On %s, grouped by column %s '%s', column %s '%s' holds -- %s." % (
            sheet.profile.name, changed.letter, changed.header or changed.letter, other.letter, other.header or other.letter, " | ".join(parts)))
    return out


def _touched(changed: List[Tuple[str, str]], trail: List[TrailCell]) -> List[Tuple[str, str]]:
    seen, out = set(), []
    for sheet, ref in list(changed) + [(t.sheet, t.cell) for t in trail if t.sheet and t.cell]:
        if (sheet, ref) not in seen:
            seen.add((sheet, ref))
            out.append((sheet, ref))
    return out[: config.WONDER_MAX_CELLS]


def build_context_facts(changed: List[Tuple[str, str]], trail: List[TrailCell], profile: WorkbookProfile, tools: WorkbookTools) -> FactsTable:
    """Facts for every cell the recording changed first, then the others it selected, capped. Empty when nothing was touched."""
    recorded: Dict[Tuple[str, str], str] = {(t.sheet, t.cell): t.content for t in trail if t.sheet and t.content}
    sheets: Dict[str, _Sheet] = {}
    deps = facts.parse_dependencies(tools) if changed else []
    items: List[Tuple[str, DataCitation]] = []
    column_done = set()
    for sheet_name, ref in _touched(changed, trail):
        sheet_profile = next((s for s in profile.sheets if s.name == sheet_name), None)
        cell = tools.cell_at(sheet_name, ref)
        if sheet_profile is None or cell is None:
            continue
        sheet = sheets.setdefault(sheet_name, _Sheet(tools, sheet_profile))
        row, col = coordinate_to_tuple(ref)
        column = next((c for c in sheet_profile.columns if c.letter == get_column_letter(col)), None)
        if column is None or row <= (sheet_profile.header_row or 0):
            continue
        cite = DataCitation(workbook=tools.name, sheet=sheet_name, ref=ref, value=cell.value, formula=cell.formula)
        is_changed = (sheet_name, ref) in set(changed)
        shown = recorded.get((sheet_name, ref))
        what = "The recording %s %s!%s (column %s '%s')" % ("changes" if is_changed else "selects", sheet_name, ref, column.letter, column.header or column.letter)
        what += " and it shows '%s'." % shown if shown else "."
        if cell.value not in (None, "") and shown and cell.value != shown:
            what += " The file holds '%s' in that cell." % cell.value
        items.append((what, cite))
        if (sheet_name, column.letter) in column_done:
            continue
        column_done.add((sheet_name, column.letter))
        items.append((_column_fact(sheet, column), cite))
        if is_changed:
            for text in _crosstab_facts(sheet, column):
                items.append((text, cite))
            readers = facts.dependents(deps, sheet_name, ref)
            items.append((
                "%d formula(s) in the workbook read %s!%s%s." % (len(readers), sheet_name, ref, ": " + " | ".join("%s!%s %s" % (d.sheet, d.ref, d.formula) for d in readers[:3]) if readers else ""),
                cite,
            ))
    cut = max(0, len(items) - config.WONDER_MAX_FACTS)
    kept = items[: config.WONDER_MAX_FACTS]
    return FactsTable(facts=[ContextFact(fact_id="F%d" % (i + 1), text=t, cell=c) for i, (t, c) in enumerate(kept)], dropped=cut)
