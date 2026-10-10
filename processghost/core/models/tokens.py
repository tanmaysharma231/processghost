"""Pydantic v2 schemas shared across the pipeline.

Reduced to TranscriptSegment alone after the Phase 3 cleanup deleted the v1 pipeline
(core/align/, core/rules/, and the intent/action/alignment/dissonance models that fed them) --
everything else the v2 pipeline needs lives in core/models/sop.py.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class TranscriptSegment(BaseModel):
    start_s: float
    end_s: float
    text: str
    speaker: Optional[str] = None
