"""Data contract for the document rung and for citations (docs/26 flow plan). Kept apart from sop.py because
the document rung has its own lifecycle (a workbook profile, tool calls, a verdict per question), and
sop.py imports from here, never the other way round."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


Rung = Literal["video", "document"]
RungOutcome = Literal["answered", "contradicted", "candidates", "no_answer", "skipped"]


class RungAttempt(BaseModel):
    """What one source of evidence did with one question. The ladder is video, then document, then the person who
    recorded it; this records the first two so a question that reaches that person carries what was already tried."""

    rung: Rung
    outcome: RungOutcome
    reason: str = ""
    answer: str = ""  # the answer itself, for outcome "answered" or "contradicted"
    event_ids: List[str] = Field(default_factory=list)  # screen events behind a video answer
    citations: List["DataCitation"] = Field(default_factory=list)  # verified cells behind a document answer
    candidates: List[str] = Field(default_factory=list)  # outcome "candidates": possible answers the data cannot choose between


class DataCitation(BaseModel):
    """One cell a data claim rests on. A citation counts only if it matches a cell a read-only tool actually
    returned for that question (core/data/checker.py); a model cannot cite a cell it was never shown."""

    workbook: str
    sheet: str
    ref: str  # cell reference such as "F27"
    value: Optional[str] = None
    formula: Optional[str] = None


class SheetProfile(BaseModel):
    """Facts about one sheet, produced by a deterministic profiler (no model). Meaning is not stated here."""

    name: str
    max_row: int = 0
    max_column: int = 0
    header_row: Optional[int] = None  # 1-based; the first row that looks like a header, not assumed to be row 1
    columns: List["ColumnProfile"] = Field(default_factory=list)
    formula_columns: List["FormulaPattern"] = Field(default_factory=list)  # one pattern per column, row index as {r}
    formula_cells: List["FormulaCell"] = Field(default_factory=list)  # formulas that are not a repeated column pattern
    merged_ranges: List[str] = Field(default_factory=list)
    empty_columns: List[str] = Field(default_factory=list)
    blank_rows: List[int] = Field(default_factory=list)


class ColumnProfile(BaseModel):
    letter: str
    header: str = ""
    kind: Literal["text", "number", "date", "formula", "mixed", "empty"] = "empty"
    non_empty: int = 0
    distinct_count: int = 0  # distinct plain (non-formula) values
    unique: bool = False  # every plain value in the column is different: a candidate identifier
    samples: List[str] = Field(default_factory=list)
    distinct: List[str] = Field(default_factory=list)  # every distinct value, only when there are few (low cardinality)
    allowed: List[str] = Field(default_factory=list)  # the options of a dropdown (data validation list) on this column, literal lists only


class FormulaPattern(BaseModel):
    column: str
    pattern: str  # the formula with its own row number replaced by {r}
    rows: List[int] = Field(default_factory=list)  # first and last row it appears on, or just the rows if few


class FormulaCell(BaseModel):
    ref: str
    formula: str
    references: List[str] = Field(default_factory=list)  # ranges the formula reads, as written


class Parameter(BaseModel):
    """A value from this recording that probably changes from run to run, decided from the workbook (core/data/params.py)."""

    name: str  # the column header it sits under
    sheet: str
    column: str
    example: str  # the value in this recording
    evidence: List[str] = Field(default_factory=list)


class FlowNote(BaseModel):
    """Something to know about reusing the flow: a value typed into a formula, a value carried in a sheet name, or what
    produces a result. Facts from the workbook with the cells they rest on; the real names and values are kept."""

    kind: Literal["hardcoded_value", "value_in_sheet_name", "result_mechanism"]
    text: str
    step_id: Optional[str] = None
    citations: List[DataCitation] = Field(default_factory=list)


class MatchIssue(BaseModel):
    kind: Literal["sheet_count", "cell_content", "text_missing"]
    detail: str
    fatal: bool = False  # a fatal issue means the workbook is not the file in the recording


class WorkbookMatch(BaseModel):
    """Whether the workbook supplied agrees with what the recording showed (core/data/match.py)."""

    verdict: Literal["match", "partial", "mismatch", "unknown"]
    checked: int = 0
    label_recall: Optional[float] = None  # share of readable words in the recording found in the matching sheet
    issues: List[MatchIssue] = Field(default_factory=list)


class WorkbookProfile(BaseModel):
    workbook: str
    sha256: str = ""
    has_cached_values: bool = False  # False: formulas were never calculated by a spreadsheet app, so no computed values exist
    sheets: List[SheetProfile] = Field(default_factory=list)
    shared_headers: List[str] = Field(default_factory=list)  # header text that appears on more than one sheet (names only)


RungAttempt.model_rebuild()
SheetProfile.model_rebuild()
