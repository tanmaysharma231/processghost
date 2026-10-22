"""Unit tests for report/build_spec.py -- deterministic triage and build_spec.json generation
from a validated SOPDocument. No API key or network call needed.
"""
from __future__ import annotations

from core.models.sop import Attribution, Flag, SOPDocument, SOPStep
from report.build_spec import (
    BLOCK_AND_ESCALATE,
    PROCEED_WITH_ASSUMPTION,
    PROCEED_WITH_FLAG,
    build_spec,
    triage_flag,
)


def _flag(**overrides) -> Flag:
    base = dict(
        flag_id="FLAG-01",
        family="ambiguity",
        question="q",
        severity="blocking",
        window_segment_ids=[0],
        step_id="step-01",
        status="open",
    )
    base.update(overrides)
    return Flag(**base)


def _step() -> SOPStep:
    return SOPStep(
        step_id="step-01",
        order=1,
        title="Do the thing",
        instruction="Do the thing.",
        attributions=[Attribution(kind="said", t_s=0.0, segment_ids=[0])],
    )


# --- triage_flag: every (status, severity) combination named in the plan -------------------

def test_resolved_always_proceeds_with_assumption_regardless_of_severity():
    assert triage_flag(_flag(status="resolved", severity="blocking")) == PROCEED_WITH_ASSUMPTION
    assert triage_flag(_flag(status="resolved", severity="note")) == PROCEED_WITH_ASSUMPTION


def test_contradicted_always_escalates():
    assert triage_flag(_flag(status="contradicted", severity="blocking")) == BLOCK_AND_ESCALATE


def test_open_blocking_escalates():
    assert triage_flag(_flag(status="open", severity="blocking")) == BLOCK_AND_ESCALATE


def test_open_caution_or_note_proceeds_with_flag():
    assert triage_flag(_flag(status="open", severity="caution")) == PROCEED_WITH_FLAG
    assert triage_flag(_flag(status="open", severity="note")) == PROCEED_WITH_FLAG


def test_unresolvable_blocking_escalates():
    """The Phase 4 acceptance case: a checked-but-unanswerable blocking question still escalates."""
    assert triage_flag(_flag(status="unresolvable", severity="blocking")) == BLOCK_AND_ESCALATE


def test_unresolvable_caution_proceeds_with_flag():
    assert triage_flag(_flag(status="unresolvable", severity="caution")) == PROCEED_WITH_FLAG


def test_unchecked_blocking_escalates():
    assert triage_flag(_flag(status="unchecked", severity="blocking")) == BLOCK_AND_ESCALATE


def test_check_failed_blocking_escalates():
    assert triage_flag(_flag(status="check_failed", severity="blocking")) == BLOCK_AND_ESCALATE


def test_check_failed_note_proceeds_with_flag():
    assert triage_flag(_flag(status="check_failed", severity="note")) == PROCEED_WITH_FLAG


# --- build_spec ----------------------------------------------------------------------------

def test_escalation_queue_contains_only_block_and_escalate_entries():
    doc = SOPDocument(
        run_id="run1",
        steps=[_step()],
        flags=[
            _flag(flag_id="FLAG-01", status="open", severity="blocking"),
            _flag(flag_id="FLAG-02", status="resolved", severity="blocking"),
            _flag(flag_id="FLAG-03", status="open", severity="note"),
        ],
    )
    spec = build_spec(doc)
    assert [f["flag_id"] for f in spec["escalation_queue"]] == ["FLAG-01"]


def test_escalation_queue_sorted_severity_first():
    """The literal Phase 4 acceptance shape: a fabricated doc mimicking the real scenario, where
    the row-matching gap (unresolvable, blocking) must land ahead of a lower-severity flag."""
    doc = SOPDocument(
        run_id="run1",
        steps=[_step()],
        flags=[
            _flag(flag_id="FLAG-CAUTION", status="open", severity="caution"),  # open + caution never escalates
            _flag(flag_id="FLAG-CONTRADICTED-NOTE", status="contradicted", severity="note"),  # escalates, lowest severity
            _flag(flag_id="FLAG-ROWMATCH", status="unresolvable", severity="blocking"),  # escalates, highest severity
        ],
    )
    spec = build_spec(doc)
    escalated = [f["flag_id"] for f in spec["escalation_queue"]]
    assert escalated == ["FLAG-ROWMATCH", "FLAG-CONTRADICTED-NOTE"]  # the sort is exercised: input order is the reverse


def test_build_spec_is_deterministic():
    doc = SOPDocument(run_id="run1", steps=[_step()], flags=[_flag()])
    assert build_spec(doc) == build_spec(doc)


def test_build_spec_includes_provenance_and_review_flags():
    doc = SOPDocument(run_id="run1", steps=[_step()], review_failed=True, timing_degraded=True)
    spec = build_spec(doc)
    assert spec["review_failed"] is True
    assert spec["timing_degraded"] is True
    assert spec["provenance_complete"] is True
