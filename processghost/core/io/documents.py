"""Ingest pre-meeting context files (plain text/markdown, .xlsx) into citable SourcePassages.

Phase 1 of docs/19-pre-meeting-intake.md -- deterministic, no LLM call anywhere in this module.
Every passage's `text` must be a literal, verifiable excerpt of the source file, because the
intake stage's grounding check (core.text_grounding.quote_is_grounded) later verifies a model's
claimed quote is a real substring of a passage's own text -- a paraphrased or synthesized passage
would make that check meaningless.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

import openpyxl

from core import config
from core.models.discovery import SourceDocument, SourcePassage

_CELL_REF_RE = re.compile(r"(\$?[A-Z]{1,3}\$?)\d+")


def load_source(path: Path, *, source_id: str) -> SourceDocument:
    """Dispatch on suffix. Raises ValueError on an unsupported suffix -- an unreadable/unsupported
    file must be a loud, catchable failure at the call site, never a silently empty source."""
    suffix = path.suffix.lower()
    if suffix not in config.INTAKE_ALLOWED_SUFFIXES:
        raise ValueError(f"unsupported context file suffix {suffix!r} for {path.name!r}")

    if suffix == ".xlsx":
        passages, ingest_note = _load_spreadsheet(path, source_id)
        kind = "spreadsheet"
    else:
        passages, ingest_note = _load_text(path, source_id)
        kind = "text"

    return SourceDocument(source_id=source_id, name=path.name, kind=kind, passages=passages, ingest_note=ingest_note)


def _load_text(path: Path, source_id: str) -> Tuple[List[SourcePassage], str]:
    """One passage per paragraph (consecutive non-blank lines coalesced), locator "lines a-b".
    Paragraph rather than line granularity: normalize_for_quote_match collapses whitespace, so a
    paragraph passage tolerates a model reflowing it, while a line-level quote spanning a line
    break would be ungroundable."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    passages: List[SourcePassage] = []
    para_start = None
    buffer: List[str] = []
    n = 0

    def _flush(end_line: int) -> None:
        nonlocal n
        if not buffer:
            return
        n += 1
        passages.append(
            SourcePassage(
                passage_id=f"{source_id}-P{n:03d}",
                source_id=source_id,
                locator=f"lines {para_start}-{end_line}",
                text=" ".join(buffer).strip(),
            )
        )
        buffer.clear()

    for i, line in enumerate(lines, start=1):
        if line.strip():
            if para_start is None:
                para_start = i
            buffer.append(line.strip())
        else:
            _flush(i - 1)
            para_start = None
    _flush(len(lines))

    return passages, ""


def _normalize_formula_pattern(formula: str) -> str:
    """Strip row numbers from cell references so two formulas that differ only by which row they
    are on (e.g. "=IF(F2<>...)" vs "=IF(F3<>...)") are recognized as the same pattern -- otherwise
    a column of 24 identical relative formulas would emit 24 redundant passages instead of one."""
    return _CELL_REF_RE.sub(lambda m: m.group(1), formula)


def _load_spreadsheet(path: Path, source_id: str) -> Tuple[List[SourcePassage], str]:
    """Two loads are required: data_only=False for formula strings, since the formulas themselves
    -- not just their cached results -- are the load-bearing content (a status value a total's
    formula depends on, a column that must not be manually overwritten). One header passage per
    sheet (naming the sheet and its column letters, since a column letter is the actual answer to
    "which column does this go in"), then one passage per distinct formula pattern per sheet,
    grouped into contiguous ranges so a repeated relative formula collapses to one passage.
    """
    wf = openpyxl.load_workbook(path, data_only=False)
    passages: List[SourcePassage] = []
    ingest_notes: List[str] = []
    n = 0

    def _next_id() -> str:
        nonlocal n
        n += 1
        return f"{source_id}-P{n:03d}"

    for ws in wf.worksheets:
        header_cells = ws[1] if ws.max_row >= 1 else []
        headers = [
            f"{cell.column_letter}={cell.value}" for cell in header_cells if cell.value is not None
        ]
        passages.append(
            SourcePassage(
                passage_id=_next_id(),
                source_id=source_id,
                locator=f"{ws.title}!1:1",
                text=(
                    f"Sheet '{ws.title}' has {ws.max_row} rows and {ws.max_column} columns. "
                    f"Header row: {' | '.join(headers)}"
                ),
            )
        )

        # (column_letter, normalized_pattern) -> [(row, coordinate, formula_text), ...]
        groups: dict = {}
        row_cap = config.INTAKE_MAX_ROWS_PER_SHEET
        truncated_rows = False
        for row in ws.iter_rows(min_row=2, max_row=min(ws.max_row, 1 + row_cap)):
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    pattern = _normalize_formula_pattern(cell.value)
                    key = (cell.column_letter, pattern)
                    groups.setdefault(key, []).append((cell.row, cell.coordinate, cell.value))
        if ws.max_row - 1 > row_cap:
            truncated_rows = True

        for (col_letter, _pattern), cells in groups.items():
            cells.sort(key=lambda c: c[0])
            representative_row, representative_coord, representative_formula = cells[0]
            if len(cells) == 1:
                locator = f"{ws.title}!{representative_coord}"
                text = f"{representative_coord} formula: {representative_formula}"
            else:
                last_coord = cells[-1][1]
                locator = f"{ws.title}!{representative_coord}:{last_coord}"
                text = (
                    f"{representative_coord} formula: {representative_formula} "
                    f"(same pattern, with row-relative references, repeats across {locator})"
                )
            passages.append(
                SourcePassage(passage_id=_next_id(), source_id=source_id, locator=locator, text=text)
            )

        if truncated_rows:
            ingest_notes.append(f"sheet '{ws.title}' truncated to first {row_cap} data rows")

    return passages, "; ".join(ingest_notes)
