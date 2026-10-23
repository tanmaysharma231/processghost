"""Parse LLM JSON-mode responses into the pipeline's Pydantic models.

Kept separate from core/pipeline.py so the parsing logic (the part that can actually be wrong --
malformed JSON, a missing field, a type that doesn't validate) is unit-testable against canned
response strings, with no live API call required. Each function raises on bad input; callers in
core/pipeline.py are responsible for catching and falling back to the heuristic path.
"""
from __future__ import annotations

import json
from typing import List

from core.slots import add_slot_flags, applicable_slots
from core.text_grounding import normalize_for_quote_match, quote_is_grounded
from core.models.sop import (
    Attribution,
    AppendixSegment,
    Flag,
    ReviewerOutput,
    SlotFill,
    SOPDocument,
    SOPStep,
    StepTransition,
    WriterOutput,
)
from pydantic import ValidationError


class LLMResponseParseError(ValueError):
    """Raised when an LLM response cannot be turned into the expected model."""


def build_sop_from_writer_output(output: WriterOutput, run_id: str, segments: List[dict]) -> SOPDocument:
    """Turn a validated WriterOutput (core/models/sop.py; produced by
    LLMClient/AnthropicClient.structured_chat -- see core/llm/client.py) into a SOPDocument.

    Shape validation is pydantic's job now (WriterOutput's schema), done before this function is
    even called. What's left here is the one business rule shape validation can't express: every
    input segment index must be accounted for exactly once, in some step's attributions or in
    appendix_segments. Raises LLMResponseParseError on a missing segment or a provenance violation
    caught from SOPDocument's own validator. The Writer stage is fatal on any failure (see
    core/pipeline.py: WriterFailedError) -- there is no per-step fallback.
    """
    steps: List[SOPStep] = []
    accounted_for: set[int] = set()
    for item in output.steps:
        attributions = []
        for a in item.attributions:
            accounted_for.update(a.segment_ids)
            attributions.append(Attribution(kind="said", t_s=a.t_s, segment_ids=a.segment_ids))
        steps.append(
            SOPStep(
                step_id=item.step_id,
                order=item.order,
                title=item.title,
                instruction=item.instruction,
                attributions=attributions,
            )
        )

    appendix: List[AppendixSegment] = []
    for item in output.appendix_segments:
        accounted_for.add(item.segment_id)
        appendix.append(
            AppendixSegment(segment_id=item.segment_id, t_s=item.t_s, text=item.text, reason=item.reason)
        )

    expected_ids = {i for i in range(len(segments))}
    missing = expected_ids - accounted_for
    if missing:
        raise LLMResponseParseError(f"Segment index(es) unaccounted for: {sorted(missing)}")

    try:
        return SOPDocument(run_id=run_id, steps=steps, appendix_segments=appendix)
    except ValidationError as exc:
        raise LLMResponseParseError(f"Writer output failed provenance validation: {exc}") from exc


# Grounding primitive moved to core/text_grounding.py (needed there too, by the intake stage,
# without importing core.llm.parsing and its sop-model dependencies). Kept as private aliases here
# so every existing call site and test reference in this module stays valid unchanged.
_normalize_for_quote_match = normalize_for_quote_match
_quote_is_grounded = quote_is_grounded


def merge_reviewer_output(
    output: ReviewerOutput, doc: SOPDocument, segments: List[dict] | None = None
) -> SOPDocument:
    """Turn a validated ReviewerOutput (core/models/sop.py) into `doc` with `transitions` and
    `flags` populated.

    This is where the coverage check lives -- the actual gap-detection mechanism (see
    core/models/sop.py: StepTransition docstring and docs/14-writer-reviewer-checker.md). The
    Reviewer prompt asks the model to emit a StepTransition ONLY where it can honestly name the
    state that carries between two consecutive steps, and to leave a pair out otherwise -- there is
    no "ok"/"broken" field for the model to self-report. This function walks doc.steps in `order`
    and, for every consecutive pair with no matching transition, synthesizes a `structural` Flag
    itself. The judgement (which pairs connect) stays entirely with the model; this function only
    counts.

    `segments` (the raw transcript segments, each with a "text" field -- the same shape
    core/pipeline.py already has on hand) grounds that judgement in something checkable: a
    transition only counts as covered if its "quote" field actually occurs in the transcript
    (see _quote_is_grounded). Before this check existed, silence (an omitted or empty-`carries`
    row) was the only signal a gap existed, and it turned out to be unreliable -- the model's own
    laziness and a genuinely unbridgeable pair produced the exact same silence. See
    docs/18-known-gaps-v2.md entry 1. `segments=None` (e.g. an old caller, or a test not exercising
    this path) disables the check and falls back to the pre-quote behavior, so this stays backward
    compatible rather than a breaking change to every call site at once.

    Any transition the model emitted between two step_ids that are NOT consecutive in `order` is
    dropped rather than trusted -- the coverage check only reasons about the fixed narration-order
    chain, so an out-of-order or duplicate transition can't be used to paper over a real gap.

    Raises LLMResponseParseError on a provenance violation caught from SOPDocument's own
    validator. Non-fatal at the call site (see core/pipeline.py) -- unlike the Writer, a Reviewer
    failure sets review_failed=True and ships zero flags rather than aborting the run.
    """
    ordered_steps = sorted(doc.steps, key=lambda s: s.order)
    consecutive_pairs = [
        (ordered_steps[i].step_id, ordered_steps[i + 1].step_id)
        for i in range(len(ordered_steps) - 1)
    ]
    transcript_text = " ".join(seg.get("text", "") for seg in segments) if segments is not None else None

    model_transitions: List[StepTransition] = []
    for item in output.transitions:
        carries = (item.carries or "").strip()
        if not carries:
            # No honest answer -- treat exactly like an omitted row, not an error.
            continue
        if transcript_text is not None and not _quote_is_grounded(item.quote, transcript_text):
            # A claimed connection with no real quote behind it is exactly as untrustworthy as
            # silence -- do not let it count as coverage.
            continue
        model_transitions.append(
            StepTransition(
                from_step_id=item.from_step_id,
                to_step_id=item.to_step_id,
                carries=carries,
                quote=(item.quote or "").strip() or carries,
                condition=item.condition,
            )
        )

    # Drop anything not on the fixed narration-order chain -- the coverage check only trusts
    # transitions between steps that are actually consecutive.
    transitions = [t for t in model_transitions if (t.from_step_id, t.to_step_id) in consecutive_pairs]
    covered = {(t.from_step_id, t.to_step_id) for t in transitions}

    flags: List[Flag] = []
    for i, item in enumerate(output.flags):
        between = tuple(item.between_step_ids) if item.between_step_ids else None
        flags.append(
            Flag(
                flag_id=f"FLAG-{i + 1:02d}",
                family=item.family,
                question=item.question,
                severity=item.severity,
                window_segment_ids=item.window_segment_ids,
                step_id=item.step_id,
                between_step_ids=between,
                slot=item.slot if item.step_id else None,
            )
        )

    # Pairs the model already raised its own structural flag for -- see the loop above. A
    # model-emitted flag is a real, specific question; the synthesized flag below is a generic
    # "the reviewer could not identify..." placeholder. Without this, a pair the model both
    # (a) noticed as a specific structural problem and (b) separately failed to name a covering
    # transition for would get BOTH flags, contradicting each other once the Checker resolves one
    # of them (a real, observed shape -- see docs/18-known-gaps-v2.md). The model's flag wins;
    # the synthesized one for that pair is skipped rather than emitted alongside it.
    model_flagged_pairs = {
        f.between_step_ids for f in flags if f.family == "structural" and f.between_step_ids
    }

    # The coverage check itself: every consecutive step pair not covered by a transition, and not
    # already flagged directly by the model, gets a synthesized structural flag. Plain counting
    # over `covered`, no LLM judgement.
    next_flag_num = len(flags) + 1
    for from_id, to_id in consecutive_pairs:
        if (from_id, to_id) in covered or (from_id, to_id) in model_flagged_pairs:
            continue
        window = _bounding_segment_ids(ordered_steps, from_id, to_id)
        flags.append(
            Flag(
                flag_id=f"FLAG-{next_flag_num:02d}",
                family="structural",
                question=(
                    f"What happens between {from_id} and {to_id}? The reviewer could not "
                    "identify what carries over between these two steps."
                ),
                severity="blocking",
                window_segment_ids=window,
                between_step_ids=(from_id, to_id),
                origin="coverage",
            )
        )
        next_flag_num += 1

    try:
        merged = doc.model_copy(update={"transitions": transitions, "flags": flags})
    except ValidationError as exc:
        raise LLMResponseParseError(f"Reviewer output failed provenance validation: {exc}") from exc
    return add_slot_flags(_apply_step_slots(merged, output, transcript_text))


def _apply_step_slots(doc: SOPDocument, output: ReviewerOutput, transcript_text: str | None) -> SOPDocument:
    """Record each step's kind and the slot answers the speaker actually gave. An answer counts only with a
    verbatim transcript quote (the same rule as transitions); an ungrounded or empty claim leaves the slot open,
    and add_slot_flags then turns every open applicable slot into a question. `transcript_text=None` (no
    transcript supplied) accepts a claim that carries any quote, as the transition check does."""
    claims = {rs.step_id: rs for rs in output.steps}
    steps = []
    for step in doc.steps:
        rs = claims.get(step.step_id)
        if rs is None or rs.kind is None:
            steps.append(step)
            continue
        slots = dict(step.slots)
        for name in applicable_slots(rs.kind):
            claim = getattr(rs, name)
            value = (claim.value if claim else "").strip()
            quote = (claim.quote if claim else "").strip()
            if not value or not quote:
                continue
            if transcript_text is not None and not _quote_is_grounded(quote, transcript_text):
                continue
            slots.setdefault(name, SlotFill(value=value, by="said", quote=quote))
        steps.append(step.model_copy(update={"kind": rs.kind, "slots": slots}))
    return doc.model_copy(update={"steps": steps})


def _bounding_segment_ids(ordered_steps: List[SOPStep], from_id: str, to_id: str) -> List[int]:
    """Best-effort window for a synthesized structural flag: the last attributed segment of the
    step before the gap and the first attributed segment of the step after it. Used only so the
    flag has a non-empty window (provenance requires one) -- the Checker (Phase 3) still needs to
    widen this into an actual frame-extraction window."""
    by_id = {s.step_id: s for s in ordered_steps}
    ids: List[int] = []
    before = by_id.get(from_id)
    after = by_id.get(to_id)
    if before and before.attributions:
        ids.extend(before.attributions[-1].segment_ids[-1:])
    if after and after.attributions:
        ids.extend(after.attributions[0].segment_ids[:1])
    return ids
