"""Pydantic v2 schemas for Process Integrity."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field


class TranscriptSegment(BaseModel):
    start_s: float
    end_s: float
    text: str
    speaker: Optional[str] = None


class EntityReference(BaseModel):
    raw_mention: str
    entity_role: str  # location_or_container | target_field | data_value | condition_or_status | actor | other
    named_specifically: bool


class IntentToken(BaseModel):
    intent_id: str
    t_s: float
    span_s: float = 0.0
    raw_text: str
    intent: str
    verb: Optional[str] = None
    obj: Optional[str] = None
    confidence: float = 0.0
    is_vague: bool = False
    entities_referenced: List[EntityReference] = Field(default_factory=list)
    selection_criteria: Optional[str] = None
    claimed_outcome: Optional[str] = None


class ActionToken(BaseModel):
    action_id: str
    t_s: float
    frame_path: Path
    action_type: str
    target: Optional[str] = None
    details: Optional[str] = None
    app: Optional[str] = None
    confidence: float = 0.0
    roi: Optional[dict] = None


class Alignment(BaseModel):
    action_id: str
    intent_id: Optional[str] = None
    match_score: float
    reason: str
    notes: Optional[str] = None


class Evidence(BaseModel):
    t_s: float
    frame_paths: List[Path] = Field(default_factory=list)
    transcript_excerpt: str = ""
    transcript_range_s: Optional[tuple[float, float]] = None
    extra: Optional[dict] = None


class Dissonance(BaseModel):
    dissonance_id: str
    kind: str
    severity: str
    t_s: float
    title: str
    explanation: str
    action_ids: List[str] = Field(default_factory=list)
    intent_ids: List[str] = Field(default_factory=list)
    evidence: Evidence
    rule_id: str
    rule_version: str = "0.1"


class UnresolvedEntityFinding(BaseModel):
    intent_id: str
    raw_mention: str
    reason: str


class UnconfirmedClaimFinding(BaseModel):
    intent_id: str
    claimed_outcome: str
    reason: str


class RunArtifacts(BaseModel):
    run_id: str
    video_path: Path
    audio_path: Path
    frames_dir: Path
    transcript: List[TranscriptSegment] = Field(default_factory=list)
    intents: List[IntentToken] = Field(default_factory=list)
    actions: List[ActionToken] = Field(default_factory=list)
    alignments: List[Alignment] = Field(default_factory=list)
    unresolved_entities: List[UnresolvedEntityFinding] = Field(default_factory=list)
    unconfirmed_claims: List[UnconfirmedClaimFinding] = Field(default_factory=list)
    dissonances: List[Dissonance] = Field(default_factory=list)
    report_md: Optional[Path] = None
    report_html: Optional[Path] = None
    metrics: dict = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True
