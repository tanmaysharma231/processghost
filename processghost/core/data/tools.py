"""Read-only workbook tools for the document rung (docs/26 flow plan).

Two tools, nothing else: `read_rows` (a block of rows) and `query_rows` (rows matching filters, optionally only
some columns). Guarantees, each enforced here and tested:

- READ-ONLY. The workbook is copied into the run directory and only the copy is opened; this module has no save
  path; the original's sha256 is recorded at the start and checked again by `verify_unchanged`.
- ROW-CAPPED. A call returns at most DATA_ROW_CAP rows; `matched_count` still reports how many matched, so "none
  of the rows have this value" is answerable without reading everything.
- BOUNDED CELLS. Every cell string is cut to DATA_CELL_MAX_CHARS. Cell contents are data, never instructions: they
  are only ever returned inside the structured result below.
- NO EXCEPTIONS TO THE CALLER. A bad sheet, column or operator is an error result the model can read and correct,
  and the caller counts it as a wasted call.

Formulas come back as written (`formula`); `value` is the cached result when the file has one, otherwise empty.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, Iterator, List, Literal, Optional, Tuple

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_to_tuple
from pydantic import BaseModel, Field

from core import config
from core.data.profile import clip, detect_header_row, is_formula
from core.io.storage import file_sha256
from core.text_grounding import to_number
from core.text_grounding import compact

Op = Literal["eq", "ne", "contains", "in", "is_empty", "not_empty", "gt", "lt"]


class Filter(BaseModel):
    column: str  # a header or a column letter
    op: Op
    value: Optional[str] = None  # for "in": values separated by "|"


class Cell(BaseModel):
    ref: str
    header: str = ""
    value: Optional[str] = None
    formula: Optional[str] = None


class Row(BaseModel):
    row: int
    cells: List[Cell] = Field(default_factory=list)


class ToolResult(BaseModel):
    ok: bool = True
    error: str = ""
    sheet: str = ""
    rows: List[Row] = Field(default_factory=list)
    matched_count: int = 0
    truncated: bool = False


class WorkbookTools:
    def __init__(self, source: Path, work_dir: Path):
        self.source = Path(source)
        self.sha256_original = file_sha256(self.source)
        work_dir.mkdir(parents=True, exist_ok=True)
        self.copy_path = work_dir / ("workbook_copy" + self.source.suffix.lower())
        shutil.copyfile(self.source, self.copy_path)
        self._formulas = openpyxl.load_workbook(self.copy_path, data_only=False, keep_links=False)
        self._values = openpyxl.load_workbook(self.copy_path, data_only=True, keep_links=False)
        self._headers: Dict[str, Dict[int, str]] = {}
        self._header_row: Dict[str, Optional[int]] = {}
        for ws in self._formulas.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            hr = detect_header_row(rows)
            self._header_row[ws.title] = hr
            self._headers[ws.title] = (
                {i + 1: clip(v) for i, v in enumerate(rows[hr - 1]) if v not in (None, "")} if hr else {}
            )

    @property
    def name(self) -> str:
        return self.source.name

    def verify_unchanged(self) -> bool:
        return file_sha256(self.source) == self.sha256_original

    # ---- helpers ---------------------------------------------------------------------

    def _sheet(self, sheet: str):
        if sheet in self._formulas.sheetnames:
            return self._formulas[sheet]
        folded = {compact(n): n for n in self._formulas.sheetnames}
        return self._formulas[folded[compact(sheet)]] if compact(sheet) in folded else None

    def _column(self, title: str, name: str) -> Optional[int]:
        """A header wins over a column letter: a header such as "Qty" is also a valid column name in a spreadsheet."""
        text = name.strip()
        for idx, header in self._headers[title].items():
            if compact(header) == compact(text):
                return idx
        if text.isalpha() and len(text) <= 3:
            try:
                return column_index_from_string(text.upper())
            except ValueError:
                pass
        return None

    def _cell(self, ws, row: int, col: int) -> Cell:
        raw = ws.cell(row=row, column=col).value
        header = self._headers[ws.title].get(col, "")
        ref = "%s%d" % (get_column_letter(col), row)
        if is_formula(raw):
            cached = self._values[ws.title].cell(row=row, column=col).value
            return Cell(ref=ref, header=header, formula=clip(raw), value=None if cached is None else clip(cached))
        return Cell(ref=ref, header=header, value=None if raw is None else clip(raw))

    def _row(self, ws, row: int, columns: Optional[List[int]] = None) -> Row:
        cols = columns if columns is not None else list(range(1, ws.max_column + 1))
        cells = [self._cell(ws, row, c) for c in cols]
        return Row(row=row, cells=[c for c in cells if c.value is not None or c.formula is not None])

    def _compare(self, cell: Cell, f: Filter) -> bool:
        shown = cell.value if cell.value is not None else (cell.formula or "")
        if f.op == "is_empty":
            return cell.value is None and cell.formula is None
        if f.op == "not_empty":
            return cell.value is not None or cell.formula is not None
        want = f.value or ""
        if f.op in ("gt", "lt"):
            a, b = to_number(shown), to_number(want)
            return a is not None and b is not None and (a > b if f.op == "gt" else a < b)
        if f.op == "in":
            return compact(shown) in {compact(v) for v in want.split("|")}
        if f.op == "contains":
            return compact(want) in compact(shown)
        return (compact(shown) == compact(want)) == (f.op == "eq")

    # ---- read access for the deterministic facts (core/data/facts.py) -----------------------

    @property
    def sheetnames(self) -> List[str]:
        return list(self._formulas.sheetnames)

    def cell_at(self, sheet: str, ref: str) -> Optional[Cell]:
        """One cell, with its header, or None for an unknown sheet or a malformed reference."""
        ws = self._sheet(sheet)
        if ws is None:
            return None
        try:
            row, col = coordinate_to_tuple(ref.replace("$", "").upper())
        except Exception:  # noqa: BLE001 - a bad reference is "no such cell", not an error
            return None
        return self._cell(ws, row, col)

    def max_row(self, sheet: str) -> int:
        ws = self._sheet(sheet)
        return ws.max_row if ws is not None else 0

    def data_values(self, sheet: str, letter: str) -> List[str]:
        """The distinct text values below the header in one column, in first-seen order. Numbers and formulas are left out."""
        ws = self._sheet(sheet)
        if ws is None:
            return []
        col, first = column_index_from_string(letter.upper()), (self._header_row.get(ws.title) or 0) + 1
        seen: Dict[str, None] = {}
        for row, c, cell in self.sheet_cells(ws.title):
            if c == col and row >= first and cell.formula is None and cell.value and to_number(cell.value) is None:
                seen.setdefault(cell.value, None)
        return list(seen)

    def formula_cells(self) -> Iterator[Tuple[str, str, str]]:
        """(sheet, ref, formula as written) for every formula cell in the workbook."""
        for ws in self._formulas.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if is_formula(c.value):
                        yield ws.title, c.coordinate, str(c.value)

    def sheet_cells(self, sheet: str) -> Iterator[Tuple[int, int, Cell]]:
        """(row, column index, cell) for every non-empty cell of a sheet."""
        ws = self._sheet(sheet)
        if ws is None:
            return
        for row in ws.iter_rows():
            for c in row:
                if c.value not in (None, ""):
                    yield c.row, c.column, self._cell(ws, c.row, c.column)

    # ---- the two tools ------------------------------------------------------------------

    def read_rows(self, sheet: str, start: int = 1, limit: int = 10) -> ToolResult:
        ws = self._sheet(sheet)
        if ws is None:
            return ToolResult(ok=False, error="unknown sheet %r; sheets are %s" % (sheet, self._formulas.sheetnames))
        limit = max(1, min(int(limit), config.DATA_ROW_CAP))
        start = max(1, int(start))
        last = min(ws.max_row, start + limit - 1)
        rows = [self._row(ws, r) for r in range(start, last + 1)]
        return ToolResult(sheet=ws.title, rows=rows, matched_count=max(0, ws.max_row - start + 1), truncated=ws.max_row > last)

    def query_rows(self, sheet: str, filters: List[Filter], columns: Optional[List[str]] = None, limit: int = 10) -> ToolResult:
        ws = self._sheet(sheet)
        if ws is None:
            return ToolResult(ok=False, error="unknown sheet %r; sheets are %s" % (sheet, self._formulas.sheetnames))
        resolved = []
        for f in filters:
            col = self._column(ws.title, f.column)
            if col is None:
                return ToolResult(ok=False, error="unknown column %r on sheet %r; headers are %s" % (f.column, ws.title, list(self._headers[ws.title].values())))
            resolved.append((col, f))
        projection = None
        if columns:
            projection = []
            for name in columns:
                col = self._column(ws.title, name)
                if col is None:
                    return ToolResult(ok=False, error="unknown column %r on sheet %r; headers are %s" % (name, ws.title, list(self._headers[ws.title].values())))
                projection.append(col)
        limit = max(1, min(int(limit), config.DATA_ROW_CAP))
        first = (self._header_row[ws.title] or 0) + 1
        matched, rows = 0, []
        for r in range(first, ws.max_row + 1):
            if all(self._compare(self._cell(ws, r, col), f) for col, f in resolved):
                matched += 1
                if len(rows) < limit:
                    rows.append(self._row(ws, r, projection))
        return ToolResult(sheet=ws.title, rows=rows, matched_count=matched, truncated=matched > len(rows))
