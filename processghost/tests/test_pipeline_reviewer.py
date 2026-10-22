"""Unit tests for core/pipeline.py's Reviewer stage (_review_sop) and its core mechanism, the
coverage check in core/llm/parsing.py: merge_reviewer_output. Uses the shared _FakeClient
(tests/fakes.py) so no network call or API key is needed.

The coverage check is the real gap-detection mechanism (see core/models/sop.py: StepTransition
docstring, docs/14-writer-reviewer-checker.md): the Reviewer emits a StepTransition only where it
can honestly name what carries between two consecutive steps, and a consecutive pair with no
transition becomes a synthesized structural Flag. There is no "ok"/self-reported-gap field --
these tests exercise that directly, with no LLM involved.

Phase 3 note: the Reviewer now calls client.structured_chat(...) instead of client.chat() +
extract_chat_content() + a hand-rolled JSON parser. _FakeClient.structured_chat returns a
ReviewerOutput object directly (or raises), so these tests build ReviewerOutput objects rather
than JSON strings.
"""
from __future__ import annotations

import pytest

from core.models.sop import Attribution, ReviewerOutput, SOPDocument, SOPStep
from core.pipeline import _review_sop
from tests.fakes import _FakeClient

SEGMENTS = [
    {"id": 0, "start": 0.0, "end": 3.0, "text": "We open the reference sheet."},
    {"id": 1, "start": 3.0, "end": 6.0, "text": "Copy the value."},
    {"id": 2, "start": 6.0, "end": 9.0, "text": "Paste it here."},
]


def _three_step_doc() -> SOPDocument:
    return SOPDocument(
        run_id="run1",
        steps=[
            SOPStep(
                step_id="step-01", order=1, title="Open the reference sheet",
                instruction="Open the reference sheet.",
                attributions=[Attribution(kind="said", t_s=0.0, segment_ids=[0])],
            ),
            SOPStep(
                step_id="step-02", order=2, title="Copy the value",
                instruction="Copy the value.",
                attributions=[Attribution(kind="said", t_s=3.0, segment_ids=[1])],
            ),
            SOPStep(
                step_id="step-03", order=3, title="Paste it",
                instruction="Paste it here.",
                attributions=[Attribution(kind="said", t_s=6.0, segment_ids=[2])],
            ),
        ],
    )


def _reviewer_output(transitions, flags) -> ReviewerOutput:
    return ReviewerOutput.model_validate({"transitions": transitions, "flags": flags})


def test_full_coverage_produces_no_structural_flags():
    doc = _three_step_doc()
    transitions = [
        {
            "from_step_id": "step-01", "to_step_id": "step-02",
            "carries": "the sheet is open", "quote": "We open the reference sheet.",
        },
        {
            "from_step_id": "step-02", "to_step_id": "step-03",
            "carries": "the value is on the clipboard", "quote": "Copy the value.",
        },
    ]
    client = _FakeClient(structured_response=_reviewer_output(transitions, []))

    result = _review_sop(client, doc, SEGMENTS)

    assert len(result.transitions) == 2
    assert [f for f in result.flags if f.family == "structural"] == []
    assert result.review_failed is False


def test_omitted_transition_becomes_exactly_one_structural_flag_naming_both_endpoints():
    """The core mechanism: the model simply leaves out step-02 -> step-03 (no 'ok: false', no
    self-report) and the coverage check must synthesize the gap flag itself."""
    doc = _three_step_doc()
    transitions = [
        {
            "from_step_id": "step-01", "to_step_id": "step-02",
            "carries": "the sheet is open", "quote": "We open the reference sheet.",
        },
        # step-02 -> step-03 deliberately omitted.
    ]
    client = _FakeClient(structured_response=_reviewer_output(transitions, []))

    result = _review_sop(client, doc, SEGMENTS)

    structural = [f for f in result.flags if f.family == "structural"]
    assert len(structural) == 1
    assert structural[0].between_step_ids == ("step-02", "step-03")
    assert structural[0].status == "open"


def test_transition_with_empty_carries_is_treated_as_omitted():
    doc = _three_step_doc()
    transitions = [
        {
            "from_step_id": "step-01", "to_step_id": "step-02",
            "carries": "the sheet is open", "quote": "We open the reference sheet.",
        },
        {"from_step_id": "step-02", "to_step_id": "step-03", "carries": "", "quote": "Copy the value."},
    ]
    client = _FakeClient(structured_response=_reviewer_output(transitions, []))

    result = _review_sop(client, doc, SEGMENTS)

    assert len(result.transitions) == 1
    structural = [f for f in result.flags if f.family == "structural"]
    assert len(structural) == 1
    assert structural[0].between_step_ids == ("step-02", "step-03")


def test_transition_with_fabricated_quote_is_treated_as_omitted():
    """The point of the quote requirement (docs/18-known-gaps-v2.md entry 1): a claimed
    connection with no real quote behind it must not count as coverage, even if `carries` reads as
    a perfectly plausible answer."""
    doc = _three_step_doc()
    transitions = [
        {
            "from_step_id": "step-01", "to_step_id": "step-02",
            "carries": "the sheet is open", "quote": "We open the reference sheet.",
        },
        {
            "from_step_id": "step-02", "to_step_id": "step-03",
            "carries": "the value is on the clipboard",
            "quote": "this text never appeared anywhere in the transcript",
        },
    ]
    client = _FakeClient(structured_response=_reviewer_output(transitions, []))

    result = _review_sop(client, doc, SEGMENTS)

    assert len(result.transitions) == 1
    structural = [f for f in result.flags if f.family == "structural"]
    assert len(structural) == 1
    assert structural[0].between_step_ids == ("step-02", "step-03")


def test_non_consecutive_transition_is_dropped_and_gap_still_flagged():
    """A transition the model claims between non-adjacent steps must not be trusted to cover the
    real gap between the consecutive pair."""
    doc = _three_step_doc()
    transitions = [
        {
            "from_step_id": "step-01", "to_step_id": "step-02",
            "carries": "the sheet is open", "quote": "We open the reference sheet.",
        },
        {
            "from_step_id": "step-01", "to_step_id": "step-03",
            "carries": "everything is fine", "quote": "Paste it here.",
        },
    ]
    client = _FakeClient(structured_response=_reviewer_output(transitions, []))

    result = _review_sop(client, doc, SEGMENTS)

    assert len(result.transitions) == 1
    structural = [f for f in result.flags if f.family == "structural"]
    assert len(structural) == 1
    assert structural[0].between_step_ids == ("step-02", "step-03")


def test_ambiguity_flag_is_parsed_and_assigned_an_id():
    doc = _three_step_doc()
    transitions = [
        {
            "from_step_id": "step-01", "to_step_id": "step-02",
            "carries": "the sheet is open", "quote": "We open the reference sheet.",
        },
        {
            "from_step_id": "step-02", "to_step_id": "step-03",
            "carries": "value is on clipboard", "quote": "Copy the value.",
        },
    ]
    flags = [
        {
            "family": "ambiguity",
            "question": "Which sheet?",
            "severity": "blocking",
            "step_id": "step-01",
            "window_segment_ids": [0],
        }
    ]
    client = _FakeClient(structured_response=_reviewer_output(transitions, flags))

    result = _review_sop(client, doc, SEGMENTS)

    assert len(result.flags) == 1
    assert result.flags[0].family == "ambiguity"
    assert result.flags[0].flag_id == "FLAG-01"


def test_model_emitted_structural_flag_suppresses_synthesized_duplicate_on_same_pair():
    """Real bug, observed in a real run (docs/18-known-gaps-v2.md): the model can both notice a
    specific structural problem on a pair AND separately fail to name a covering transition for
    that same pair. Without dedup, both a model-emitted flag and the coverage check's synthesized
    generic flag survive for the identical between_step_ids -- and once the Checker resolves one
    of them, the document contradicts itself (one flag says resolved, the other says
    unresolvable, for the same gap). The model's own flag -- a real, specific question -- must
    win; the generic synthesized one must not also appear."""
    doc = _three_step_doc()
    transitions = [
        {
            "from_step_id": "step-01", "to_step_id": "step-02",
            "carries": "the sheet is open", "quote": "We open the reference sheet.",
        },
        # step-02 -> step-03 deliberately left uncovered -- AND the model separately flags it
        # directly below, the real shape observed in run 20260830-160732-e1b16b.
    ]
    flags = [
        {
            "family": "structural",
            "question": "What action or process guarantees the value is on the clipboard?",
            "severity": "blocking",
            "between_step_ids": ["step-02", "step-03"],
            "window_segment_ids": [1],
        }
    ]
    client = _FakeClient(structured_response=_reviewer_output(transitions, flags))

    result = _review_sop(client, doc, SEGMENTS)

    structural = [f for f in result.flags if f.family == "structural"]
    assert len(structural) == 1, "the synthesized generic flag must not survive alongside the model's own"
    assert structural[0].between_step_ids == ("step-02", "step-03")
    assert "guarantees the value is on the clipboard" in structural[0].question, (
        "the model's specific question must be the one that survives, not the generic "
        "'the reviewer could not identify what carries over' placeholder"
    )


def test_review_sop_degrades_on_call_exception():
    doc = _three_step_doc()
    client = _FakeClient(structured_response=RuntimeError("connection reset"))

    result = _review_sop(client, doc, SEGMENTS)

    assert result.review_failed is True
    assert result.flags == []
    assert result.steps == doc.steps  # Writer's document is preserved, not discarded
    assert len(client.metrics.failures) == 1
    assert client.metrics.failures[0]["stage"] == "reviewer"


