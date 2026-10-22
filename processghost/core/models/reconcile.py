"""Structured-output target for RECONCILER_SYSTEM (docs/24): the Reconciler maps narrated steps onto
screen events and says, for every step, what changed. Separate from sop.py for the same reason
discovery.py is: it is an LLM output contract, merged into the SOPDocument by
core/screen/reconcile.py, which verifies every quote before trusting any of it."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from core.models.sop import Severity, SlotName


class QuotedEvidence(BaseModel):
    event_id: str
    quote: str  # verbatim text from that event's change record


class UnspecifiedQuestion(BaseModel):
    question: str
    severity: Severity = "caution"


class SlotFillClaim(BaseModel):
    slot: SlotName
    value: str = ""
    evidence: List[QuotedEvidence] = Field(default_factory=list)  # verbatim quotes from the cited events' records


class StepReconciliation(BaseModel):
    step_id: str
    event_ids: List[str] = Field(default_factory=list)  # events that realise this step; [] if none do
    what_changed: str = ""  # written only from the cited records; "" if no event shows this step
    evidence: List[QuotedEvidence] = Field(default_factory=list)
    unspecified: List[UnspecifiedQuestion] = Field(default_factory=list)  # still open after reading the records
    slot_fills: List[SlotFillClaim] = Field(default_factory=list)  # answers to the step's open slots, from the records


class EventAssignment(BaseModel):
    event_id: str
    step_id: Optional[str] = None  # None: no narrated step explains this event
    # Only for an event no step explains: what was done, written from the record, with verbatim quotes.
    # A grounded action makes the event a "shown" step; an ungrounded one leaves it a silent-change flag.
    action: str = ""
    evidence: List[QuotedEvidence] = Field(default_factory=list)


class ClaimCheck(BaseModel):
    step_id: str
    claim: str  # a consequence the speaker asserts ("it updates", "it saves", ...)
    supported: Literal["yes", "no", "unclear"]
    evidence: List[QuotedEvidence] = Field(default_factory=list)  # required for "yes"


class ReconcilerOutput(BaseModel):
    steps: List[StepReconciliation] = Field(default_factory=list)
    events: List[EventAssignment] = Field(default_factory=list)
    claims: List[ClaimCheck] = Field(default_factory=list)
