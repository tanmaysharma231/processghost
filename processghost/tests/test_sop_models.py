"""Unit tests for core/models/sop.py's provenance validator -- the mechanical enforcement of the
v2 provenance rule (every content line has exactly one attribution). See docs/15 and the v2 plan's
"Decisions locked" table: the validator raises rather than setting a soft bool, so an invalid
SOPDocument cannot exist.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.models.sop import (
    Attribution,
    Flag,
    FrameDescription,
    Resolution,
    SOPDocument,
    SOPStep,
    StepTransition,
)


def _step(**overrides) -> dict:
    base = dict(
        step_id="step-01",
        order=1,
        title="Open the reference sheet",
        instruction="Open the reference sheet.",
        attributions=[Attribution(kind="said", t_s=0.0, segment_ids=[0])],
    )
    base.update(overrides)
    return base


def test_step_without_attribution_raises():
    with pytest.raises(ValidationError) as excinfo:
        SOPDocument(run_id="run1", steps=[SOPStep(**_step(attributions=[]))])
    assert "provenance" in str(excinfo.value)


def test_flag_without_window_raises():
    step = SOPStep(**_step())
    with pytest.raises(ValidationError) as excinfo:
        SOPDocument(
            run_id="run1",
            steps=[step],
            flags=[
                Flag(
                    flag_id="FLAG-01",
                    family="ambiguity",
                    question="Which column?",
                    severity="blocking",
                    window_segment_ids=[],
                    window_time_range=None,
                )
            ],
        )
    assert "provenance" in str(excinfo.value)


def test_resolved_resolution_without_evidence_raises():
    step = SOPStep(**_step())
    flag = Flag(
        flag_id="FLAG-01",
        family="ambiguity",
        question="Which column?",
        severity="blocking",
        window_segment_ids=[0],
    )
    with pytest.raises(ValidationError) as excinfo:
        SOPDocument(
            run_id="run1",
            steps=[step],
            flags=[flag],
            resolutions=[Resolution(flag_id="FLAG-01", verdict="resolved", evidence=[])],
        )
    assert "provenance" in str(excinfo.value)


def test_unresolvable_resolution_without_reason_raises():
    step = SOPStep(**_step())
    flag = Flag(
        flag_id="FLAG-01",
        family="ambiguity",
        question="Which column?",
        severity="blocking",
        window_segment_ids=[0],
    )
    with pytest.raises(ValidationError) as excinfo:
        SOPDocument(
            run_id="run1",
            steps=[step],
            flags=[flag],
            resolutions=[Resolution(flag_id="FLAG-01", verdict="unresolvable", reason="")],
        )
    assert "provenance" in str(excinfo.value)


def test_unresolvable_resolution_with_reason_is_valid():
    step = SOPStep(**_step())
    flag = Flag(
        flag_id="FLAG-01",
        family="ambiguity",
        question="Which column?",
        severity="blocking",
        window_segment_ids=[0],
    )
    doc = SOPDocument(
        run_id="run1",
        steps=[step],
        flags=[flag],
        resolutions=[
            Resolution(flag_id="FLAG-01", verdict="unresolvable", reason="nothing on screen shows it")
        ],
    )
    assert doc.provenance_complete is True


def test_step_flag_id_not_in_registry_raises():
    step = SOPStep(**_step(flag_ids=["FLAG-99"]))
    with pytest.raises(ValidationError) as excinfo:
        SOPDocument(run_id="run1", steps=[step])
    assert "provenance" in str(excinfo.value)


def test_valid_document_reports_provenance_complete_true():
    step = SOPStep(**_step())
    doc = SOPDocument(run_id="run1", steps=[step])
    assert doc.provenance_complete is True
    assert "provenance_complete" in doc.model_dump()
    assert doc.model_dump()["provenance_complete"] is True


def test_step_transition_carries_cannot_be_empty():
    """`carries` is the mechanism the coverage check depends on -- a transition the Reviewer
    couldn't honestly justify must never survive as an empty string, only as an absent row."""
    with pytest.raises(ValidationError):
        StepTransition(from_step_id="step-01", to_step_id="step-02", carries="")


def test_transition_with_dangling_step_id_raises():
    step = SOPStep(**_step())
    transition = StepTransition(
        from_step_id="step-01", to_step_id="step-99",
        carries="the value is copied", quote="paste it here",
    )
    with pytest.raises(ValidationError) as excinfo:
        SOPDocument(run_id="run1", steps=[step], transitions=[transition])
    assert "provenance" in str(excinfo.value)


def test_structural_flag_between_step_ids_must_reference_real_steps():
    step = SOPStep(**_step())
    flag = Flag(
        flag_id="FLAG-01",
        family="structural",
        question="What happens between step 1 and step 2?",
        severity="blocking",
        window_segment_ids=[0],
        between_step_ids=("step-01", "step-99"),
    )
    with pytest.raises(ValidationError) as excinfo:
        SOPDocument(run_id="run1", steps=[step], flags=[flag])
    assert "provenance" in str(excinfo.value)


def test_resolved_resolution_with_evidence_is_valid():
    step = SOPStep(**_step())
    flag = Flag(
        flag_id="FLAG-01",
        family="ambiguity",
        question="Which column?",
        severity="blocking",
        window_segment_ids=[0],
    )
    doc = SOPDocument(
        run_id="run1",
        steps=[step],
        flags=[flag],
        resolutions=[
            Resolution(
                flag_id="FLAG-01",
                verdict="resolved",
                inferred_text="Column F",
                evidence=[FrameDescription(frame_path="t00014000.png", t_s=14.0)],
            )
        ],
    )
    assert doc.provenance_complete is True
