"""Data contract for the v2 Writer / Reviewer / Checker pipeline.

See Process_ghost/processghost-v2-writer-reviewer-checker-plan.md and
docs/14-writer-reviewer-checker.md. Kept separate from core/models/tokens.py (now reduced to just
TranscriptSegment, after the Phase 3 cleanup deleted the v1 models/pipeline that used to live
alongside it) purely for file size -- this module owns everything the v2 pipeline actually needs.

The core idea this contract enforces mechanically is the provenance rule: every content line in
the generated SOP carries exactly one attribution -- said, inferred, or an open flag. See
provenance_violations() below and SOPDocument's validator.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, computed_field, model_validator

from core.models.data import DataCitation, RungAttempt, WorkbookMatch
from core.models.screen import SelectionAnchor, UnusedEvent
from core.models.tokens import TranscriptSegment

Kind = Literal["said", "inferred", "shown"]
Family = Literal["ambiguity", "structural"]
Severity = Literal["blocking", "caution", "note"]
Status = Literal["open", "resolved", "contradicted", "unresolvable", "unchecked", "check_failed"]
Verdict = Literal["resolved", "contradicted", "unresolvable"]
Origin = Literal["reviewer", "coverage", "screen", "reconcile", "data"]
CheckerTier = Literal["ocr", "vision", "fallback_frames"]
SlotName = Literal["source", "key", "target", "effect"]
StepKind = Literal["navigate", "move_data", "edit", "check", "other"]
SlotBy = Literal["said", "shown", "document", "sme"]
FindingKind = Literal[
    "explained_action",  # the open question was answered from evidence
    "observed_unexplained",  # a change is visible on screen that nobody narrated
    "unresolved_requirement",  # a rule, threshold, actor or condition needed to implement is not established
    "potential_conflict",  # sources appear to disagree; may be compatible under different scopes, so ask first
    "unsupported_assertion",  # speech claims something the supplied evidence does not show (NOT disproved)
    "no_finding",  # a pipeline limitation or insufficient basis, not a statement about the process
]


class Attribution(BaseModel):
    """One `[said @t]` tag on a step -- which transcript segment(s) it came from."""

    kind: Kind
    t_s: float
    segment_ids: List[int] = Field(default_factory=list)
    frame_paths: List[Path] = Field(default_factory=list)
    event_ids: List[str] = Field(default_factory=list)  # kind "shown": the screen events that are this step


class InferredClause(BaseModel):
    """One `[inferred from screen @t]` tag, added by the Checker (Phase 3) on a resolved flag.
    Kept separate from SOPStep.instruction rather than merged into it, so the narrated text stays
    byte-stable across runs (the merge is additive, never a rewrite) -- see docs/15."""

    text: str
    t_s: float
    frame_paths: List[Path] = Field(default_factory=list)
    flag_id: str


class ObservedChange(BaseModel):
    """What the screen showed for a step, written by the Reconciler (docs/24) and kept only if every
    quote in `quotes` is a verbatim substring of the cited events' OCR'd change records -- see
    core/screen/reconcile.py. Rendered as `[observed @t]`, a third evidence kind next to said and
    inferred: it was read off the screen for a step the speaker narrated, not inserted as a new step."""

    text: str
    event_ids: List[str] = Field(default_factory=list)
    t_s: float = 0.0
    quotes: List[str] = Field(default_factory=list)


class SlotFill(BaseModel):
    """The answer to one of a data step's four questions (core/slots.py), with where the answer came from.
    `by` says which source supplied it; a said slot carries the verbatim transcript quote, a shown slot the
    screen events, a document slot verified cell citations."""

    value: str
    by: SlotBy
    quote: str = ""
    event_ids: List[str] = Field(default_factory=list)
    citations: List[DataCitation] = Field(default_factory=list)


class SOPStep(BaseModel):
    step_id: str
    order: int
    title: str
    instruction: str
    attributions: List[Attribution] = Field(default_factory=list)
    inferred_clauses: List[InferredClause] = Field(default_factory=list)
    flag_ids: List[str] = Field(default_factory=list)
    observed_changes: List[ObservedChange] = Field(default_factory=list)
    kind: Optional[StepKind] = None  # set by the Reviewer; decides which slots apply (core/slots.py)
    slots: Dict[str, SlotFill] = Field(default_factory=dict)  # slot name -> answer; an applicable slot not here is open


class AppendixSegment(BaseModel):
    """A transcript segment the Writer judged non-actionable (small talk, a pause, an aside) --
    listed here rather than silently dropped, so every input segment is accounted for somewhere."""

    segment_id: int
    t_s: float
    text: str
    reason: str = ""


class StepTransition(BaseModel):
    """A Reviewer (Phase 2) claim that one step's outcome carries into the next step's start.
    Emitted ONLY when the Reviewer can honestly name what carries across -- there is no boolean
    "ok" field and no way to mark a transition broken. A consecutive step pair with no
    StepTransition between them is how a structural gap is detected: by omission, checked
    deterministically in core.llm.parsing (coverage check), not by self-report. See
    docs/14-writer-reviewer-checker.md and the v2 plan for why self-report was rejected -- a model
    asked "is this ok?" is strongly biased toward saying yes.

    `quote` grounds `carries` in something checkable: the verbatim transcript span that
    establishes the connection. merge_reviewer_output (core/llm/parsing.py) verifies the quote
    actually occurs in the transcript before counting the pair as covered -- a model that
    genuinely cannot bridge a pair cannot produce a real quote, so a fabricated `carries` with an
    invented or absent quote is caught deterministically rather than trusted. See
    docs/18-known-gaps-v2.md entry 1 for why omission alone (no quote requirement) proved unstable
    in practice."""

    from_step_id: str
    to_step_id: str
    carries: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    condition: Optional[str] = None


class Flag(BaseModel):
    """A Reviewer (Phase 2) annotation: an ambiguity or suspected structural gap, with a window the
    Checker (Phase 3) will later resolve against frames."""

    flag_id: str
    family: Family
    question: str
    severity: Severity
    window_segment_ids: List[int] = Field(default_factory=list)
    window_time_range: Optional[tuple[float, float]] = None
    step_id: Optional[str] = None
    between_step_ids: Optional[tuple[str, str]] = None
    status: Status = "open"
    # v3 additions (docs/22): all optional so v2 documents and build_spec consumers are unaffected.
    origin: Origin = "reviewer"  # which stage created the flag
    event_ids: List[str] = Field(default_factory=list)  # screen events attached as Checker evidence
    checker_tier: Optional[CheckerTier] = None  # which evidence path produced the verdict
    expansion_level: Optional[int] = None  # 0 or 1: the Checker level that reached the final verdict
    finding_kind: Optional[FindingKind] = None  # set where the creating stage knows why the flag exists (reconcile)
    slot: Optional[SlotName] = None  # which of a data step's four questions this flag asks, when it asks one
    rungs: List[RungAttempt] = Field(default_factory=list)  # what the video and the document each did with this question


class FrameDescription(BaseModel):
    """Slimmed ActionToken -- one frame's worth of Checker evidence."""

    frame_path: Path
    t_s: float
    description: str = ""
    app: Optional[str] = None
    confidence: float = 0.0


class Resolution(BaseModel):
    """A Checker (Phase 3) verdict for one flag."""

    flag_id: str
    verdict: Verdict
    inferred_text: Optional[str] = None
    evidence: List[FrameDescription] = Field(default_factory=list)
    reason: str = ""
    narration_version: Optional[str] = None
    screen_version: Optional[str] = None
    # A deterministic fill (anchors, Reconciler slot fills, the document rung) resolves a flag with no frame
    # to show: it cites screen events or verified cells instead.
    event_ids: List[str] = Field(default_factory=list)
    citations: List[DataCitation] = Field(default_factory=list)


def provenance_violations(doc: "SOPDocument") -> List[str]:
    """Pure predicate behind SOPDocument's validator -- module-level so both the validator and
    tests can call it directly without constructing (or failing to construct) a model.

    Checks, in order:
    1. every step has >= 1 attribution
    2. every flag has a window (segment ids or a time range)
    3. a resolution with verdict resolved/contradicted has >= 1 evidence frame; a resolution with
       verdict unresolvable has a non-empty reason instead (it cannot have evidence by definition
       -- nothing on screen showed the answer)
    4. every step.flag_ids entry exists in the flag registry
    5. every resolution.flag_id exists in the flag registry
    6. every inferred_clause.flag_id (on any step) exists in the flag registry
    7. every transition's from_step_id/to_step_id is a real step_id
    8. a "structural" flag's between_step_ids, if set, both reference real steps

    These are all referential/bookkeeping checks -- they confirm ids point at real things, never
    whether a gap is "real". Whether a structural flag should exist at all is decided by the
    Reviewer's coverage check in core.llm.parsing, not here.
    """
    violations: List[str] = []
    flag_ids = {f.flag_id for f in doc.flags}
    step_ids = {s.step_id for s in doc.steps}

    for step in doc.steps:
        if not step.attributions:
            violations.append(f"step {step.step_id!r} has no attributions")
        for attr in step.attributions:
            if attr.kind == "shown" and not attr.event_ids:
                violations.append(f"step {step.step_id!r} has a shown attribution with no screen events")
        for fid in step.flag_ids:
            if fid not in flag_ids:
                violations.append(f"step {step.step_id!r} references unknown flag {fid!r}")
        for clause in step.inferred_clauses:
            if clause.flag_id not in flag_ids:
                violations.append(
                    f"step {step.step_id!r} inferred_clause references unknown flag {clause.flag_id!r}"
                )

    for flag in doc.flags:
        if not flag.window_segment_ids and flag.window_time_range is None:
            violations.append(f"flag {flag.flag_id!r} has no window")
        if flag.between_step_ids and (
            flag.between_step_ids[0] not in step_ids or flag.between_step_ids[1] not in step_ids
        ):
            violations.append(f"flag {flag.flag_id!r} between_step_ids references unknown step(s)")

    for transition in doc.transitions:
        if transition.from_step_id not in step_ids:
            violations.append(
                f"transition references unknown from_step_id {transition.from_step_id!r}"
            )
        if transition.to_step_id not in step_ids:
            violations.append(
                f"transition references unknown to_step_id {transition.to_step_id!r}"
            )

    for res in doc.resolutions:
        if res.flag_id not in flag_ids:
            violations.append(f"resolution references unknown flag {res.flag_id!r}")
        if res.verdict in ("resolved", "contradicted") and not (res.evidence or res.event_ids or res.citations):
            violations.append(
                f"resolution for {res.flag_id!r} has verdict {res.verdict!r} but no evidence"
            )
        if res.verdict == "unresolvable" and not res.reason.strip():
            violations.append(f"resolution for {res.flag_id!r} is unresolvable but has no reason")

    return violations


# Version of the sop.json / build_spec.json output contract. Bump the major number when a field
# is removed or changes meaning; adding optional fields does not require a bump.
SCHEMA_VERSION = "3.1"


class ScreenCoverage(BaseModel):
    """How much of what the screen path wanted to look at was actually examined (screen runs only)."""

    total: int = 0
    examined: int = 0
    skipped_budget: int = 0
    skipped_cap: int = 0
    unexamined: int = 0
    not_examined_spans: List[tuple[float, float]] = Field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.examined == self.total


class SOPDocument(BaseModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    steps: List[SOPStep] = Field(default_factory=list)
    transitions: List[StepTransition] = Field(default_factory=list)
    flags: List[Flag] = Field(default_factory=list)
    resolutions: List[Resolution] = Field(default_factory=list)
    appendix_segments: List[AppendixSegment] = Field(default_factory=list)
    review_failed: bool = False
    timing_degraded: bool = False
    screen_coverage: Optional[ScreenCoverage] = None
    # Screen-side additions (docs/26 flow plan): both default empty so older sop.json files load.
    anchors: List[SelectionAnchor] = Field(default_factory=list)  # selections kept as pointers for speech and later values
    screen_activity_unused: List[UnusedEvent] = Field(default_factory=list)  # events that became no step, with the reason
    workbook_match: Optional[WorkbookMatch] = None  # whether the workbook supplied agrees with the recording (with --workbook)

    @computed_field  # type: ignore[misc]
    @property
    def provenance_complete(self) -> bool:
        """Derived, not settable -- always reflects provenance_violations(self). Serializes into
        sop.json / build_spec.json so an external auditor can check it without re-deriving it, but
        cannot be spoofed by input since it is never a constructor argument."""
        return not provenance_violations(self)

    @model_validator(mode="after")
    def _enforce_provenance(self) -> "SOPDocument":
        """Raises rather than setting a soft flag -- an invalid SOPDocument cannot exist. This
        makes a Writer that omits an attribution fail at construction, which is exactly the one
        fatal stage per the owner's plan; every downstream consumer (renderer, later phases' merge
        and build_spec) can then assume provenance holds without re-checking."""
        violations = provenance_violations(self)
        if violations:
            raise ValueError("provenance violations: " + "; ".join(violations))
        return self


class WriterOutputAttribution(BaseModel):
    """Shape-only mirror of WRITER_SYSTEM's per-attribution JSON, used as a
    with_structured_output()/model_validate() target -- see core/llm/client.py: structured_chat.
    Kept separate from Attribution (which has kind/frame_paths, Checker-era fields the Writer never
    produces) so the LLM-facing schema and the internal model can drift independently."""

    t_s: float
    segment_ids: List[int] = Field(default_factory=list)


class WriterOutputStep(BaseModel):
    step_id: str
    order: int
    title: str = ""
    instruction: str = ""
    attributions: List[WriterOutputAttribution] = Field(default_factory=list)


class WriterOutputAppendixSegment(BaseModel):
    segment_id: int
    t_s: float = 0.0
    text: str = ""
    reason: str = ""


class WriterOutput(BaseModel):
    """Structured-output target for WRITER_SYSTEM, field-for-field the JSON shape the prompt asks
    for. build_sop_from_writer_output() (core/llm/parsing.py) turns this into a validated
    SOPDocument, applying the segment-coverage business rule that shape validation alone can't
    express."""

    steps: List[WriterOutputStep] = Field(default_factory=list)
    appendix_segments: List[WriterOutputAppendixSegment] = Field(default_factory=list)


class ReviewerOutputTransition(BaseModel):
    from_step_id: str
    to_step_id: str
    carries: str = ""
    quote: str = ""
    condition: Optional[str] = None


class SlotClaim(BaseModel):
    """What the speaker said about one slot of a step, with the verbatim words it rests on."""

    value: str = ""
    quote: str = ""


class ReviewerOutputStep(BaseModel):
    step_id: str
    kind: Optional[StepKind] = None
    source: Optional[SlotClaim] = None
    key: Optional[SlotClaim] = None
    target: Optional[SlotClaim] = None
    effect: Optional[SlotClaim] = None


class ReviewerOutputFlag(BaseModel):
    family: Family
    question: str = ""
    severity: Severity = "caution"
    slot: Optional[SlotName] = None  # set when the question is about one of the step's four slots
    step_id: Optional[str] = None
    between_step_ids: Optional[List[str]] = None
    window_segment_ids: List[int] = Field(default_factory=list)


class ReviewerOutput(BaseModel):
    """Structured-output target for REVIEWER_SYSTEM. merge_reviewer_output() (core/llm/parsing.py)
    turns this into flags/transitions on the SOPDocument, applying the transition coverage-check
    that is the actual gap-detection mechanism -- see StepTransition's docstring."""

    transitions: List[ReviewerOutputTransition] = Field(default_factory=list)
    flags: List[ReviewerOutputFlag] = Field(default_factory=list)
    steps: List[ReviewerOutputStep] = Field(default_factory=list)


class CheckerOutput(BaseModel):
    """Structured-output target for CHECKER_SYSTEM -- one call per flag. `evidence` deliberately
    isn't asked for as a set of frame timestamps the model picks out: every frame extracted for
    the flag's window is used as evidence on a resolved/contradicted verdict (core/pipeline.py:
    _apply_resolution), which is enough to satisfy the provenance rule (>=1 evidence frame) without
    asking the model to self-report which frame mattered -- a judgment call not worth the schema
    complexity given how narrow each window already is.

    `observed` is filled in BEFORE `verdict`/`inferred_text` -- same forcing-function shape as
    StepTransition.carries and the Reviewer's two-pass ambiguity check: writing down concretely
    what's legible on screen first (labels, headers, highlighted cells, tab names, or literal OCR
    text) makes it harder for the model to jump straight to a plausible-sounding "resolved" without
    actually grounding it in something visible. See docs/14 and this session's history for why a
    single verdict+answer field allowed vague-but-confident resolutions to slip through."""

    observed: str = ""
    verdict: Verdict
    inferred_text: Optional[str] = None
    reason: str = ""
    narration_version: Optional[str] = None
    screen_version: Optional[str] = None


class RunArtifacts(BaseModel):
    """The run container returned by core.pipeline.run_pipeline."""

    run_id: str
    video_path: Path
    audio_path: Path
    frames_dir: Path
    checker_frames_dir: Optional[Path] = None
    transcript: List[TranscriptSegment] = Field(default_factory=list)
    sop: Optional[SOPDocument] = None
    sop_draft_md: Optional[Path] = None
    sop_md: Optional[Path] = None
    build_spec_path: Optional[Path] = None
    metrics: dict = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True
