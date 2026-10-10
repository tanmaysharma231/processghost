"""Data contract for pre-meeting discovery: source documents, the intake model built from them,
and the requirements artifact built from a finished run. Kept out of core/models/sop.py -- sop.py
owns the recording's document model and must not change at all so sop.md stays byte-identical to
today; discovery has its own lifecycle (built pre-run, updated post-run, versioned across runs).

Phase 1 of docs/19-pre-meeting-intake.md: only the source layer (SourceDocument/SourcePassage) is
deterministic, no-LLM ground truth. The intake model itself (KnownFact, OpenQuestion, ...) and the
requirements model are added in later phases once there is an LLM output schema to build them from.
"""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

SourceKind = Literal["text", "spreadsheet"]


class SourcePassage(BaseModel):
    """One citable, quotable unit of a source document. `locator` is human-checkable against the
    original file: "lines 12-18" for text, "<Sheet>!F27" or "<Sheet>!G2:G25" for a spreadsheet."""

    passage_id: str
    source_id: str
    locator: str
    text: str = Field(min_length=1)


class SourceDocument(BaseModel):
    """One ingested pre-meeting file. `passages` is populated by core.io.documents.load_source;
    kept here (rather than only in a separate list) so a passage's source is self-describing."""

    source_id: str
    name: str
    kind: SourceKind
    passages: List[SourcePassage] = Field(default_factory=list)
    ingest_note: str = ""
