"""Unit tests for core/pipeline.py's Checker stage (Phase 3): flag selection, window math, and
merge-back. Uses the shared _FakeClient (tests/fakes.py) so no network call, API key, or real
ffmpeg is needed -- frame extraction itself (core/io/video.py: extract_frames_window) is exercised
separately in tests/test_video_windows.py against a real fixture video.
"""
from __future__ import annotations

from pathlib import Path

from core import config
from core.models.sop import (
    Attribution,
    CheckerOutput,
    Flag,
    SOPDocument,
    SOPStep,
    StepTransition,
)
from core.models.tokens import TranscriptSegment
from core.pipeline import (
    _apply_resolution,
    _flag_window_s,
    _select_flags_for_check,
)


def _step(step_id, order, title, t_s, segment_ids) -> SOPStep:
    return SOPStep(
        step_id=step_id,
        order=order,
        title=title,
        instruction=title,
        attributions=[Attribution(kind="said", t_s=t_s, segment_ids=segment_ids)],
    )


def _two_step_doc(**overrides) -> SOPDocument:
    steps = [
        _step("step-01", 1, "Open the sheet", 0.0, [0]),
        _step("step-02", 2, "Paste it", 20.0, [1]),
    ]
    base = dict(run_id="run1", steps=steps)
    base.update(overrides)
    return SOPDocument(**base)


# --- flag selection ----------------------------------------------------------

def test_selection_orders_by_severity_blocking_first():
    doc = _two_step_doc(
        flags=[
            Flag(flag_id="FLAG-01", family="ambiguity", question="q1", severity="note", window_segment_ids=[0], step_id="step-01"),
            Flag(flag_id="FLAG-02", family="ambiguity", question="q2", severity="blocking", window_segment_ids=[0], step_id="step-01"),
            Flag(flag_id="FLAG-03", family="ambiguity", question="q3", severity="caution", window_segment_ids=[0], step_id="step-01"),
        ]
    )
    checked, unchecked = _select_flags_for_check(doc)
    assert [f.flag_id for f in checked] == ["FLAG-02", "FLAG-03", "FLAG-01"]
    assert unchecked == []


def test_selection_caps_at_max_checked_flags(monkeypatch):
    monkeypatch.setattr(config, "MAX_CHECKED_FLAGS", 2)
    doc = _two_step_doc(
        flags=[
            Flag(flag_id=f"FLAG-{i:02d}", family="ambiguity", question="q", severity="blocking", window_segment_ids=[0], step_id="step-01")
            for i in range(1, 5)
        ]
    )
    checked, unchecked = _select_flags_for_check(doc)
    assert len(checked) == 2
    assert len(unchecked) == 2


def test_selection_skips_non_open_flags():
    doc = _two_step_doc(
        flags=[
            Flag(flag_id="FLAG-01", family="ambiguity", question="q", severity="blocking", window_segment_ids=[0], step_id="step-01", status="resolved"),
        ]
    )
    checked, unchecked = _select_flags_for_check(doc)
    assert checked == []
    assert unchecked == []


# --- window math ---------------------------------------------------------------

def _transcript():
    return [
        TranscriptSegment(start_s=0.0, end_s=3.0, text="a"),
        TranscriptSegment(start_s=10.0, end_s=12.0, text="b"),
    ]


def test_window_from_segment_ids_applies_lead_in_and_min_width(monkeypatch):
    monkeypatch.setattr(config, "CHECKER_LEAD_IN_S", 3.0)
    monkeypatch.setattr(config, "CHECKER_MIN_WINDOW_S", 4.0)
    doc = _two_step_doc()
    flag = Flag(flag_id="FLAG-01", family="ambiguity", question="q", severity="blocking", window_segment_ids=[1], step_id="step-02")
    start, end = _flag_window_s(flag, doc, _transcript())
    assert start == 7.0  # 10.0 - lead_in
    assert end == 12.0  # segment end, already >= min width from start


def test_window_clamps_start_to_zero(monkeypatch):
    monkeypatch.setattr(config, "CHECKER_LEAD_IN_S", 5.0)
    doc = _two_step_doc()
    flag = Flag(flag_id="FLAG-01", family="ambiguity", question="q", severity="blocking", window_segment_ids=[0], step_id="step-01")
    start, end = _flag_window_s(flag, doc, _transcript())
    assert start == 0.0


def test_window_enforces_minimum_width(monkeypatch):
    monkeypatch.setattr(config, "CHECKER_LEAD_IN_S", 0.0)
    monkeypatch.setattr(config, "CHECKER_MIN_WINDOW_S", 10.0)
    doc = _two_step_doc()
    flag = Flag(flag_id="FLAG-01", family="ambiguity", question="q", severity="blocking", window_segment_ids=[0], step_id="step-01")
    start, end = _flag_window_s(flag, doc, _transcript())
    assert end - start == 10.0


def test_window_uses_explicit_time_range_over_segment_ids():
    doc = _two_step_doc()
    flag = Flag(
        flag_id="FLAG-01", family="ambiguity", question="q", severity="blocking",
        window_time_range=(50.0, 55.0), window_segment_ids=[0], step_id="step-01",
    )
    start, end = _flag_window_s(flag, doc, _transcript())
    assert start <= 50.0
    assert end >= 55.0


def test_structural_flag_falls_back_to_bounding_step_when_no_window_ids(monkeypatch):
    monkeypatch.setattr(config, "CHECKER_LEAD_IN_S", 0.0)
    monkeypatch.setattr(config, "CHECKER_FALLBACK_WINDOW_S", 30.0)
    doc = _two_step_doc()
    flag = Flag(
        flag_id="FLAG-01", family="structural", question="q", severity="blocking",
        window_segment_ids=[], between_step_ids=("step-01", "step-02"),
    )
    start, end = _flag_window_s(flag, doc, _transcript())
    assert start == 0.0  # step-01's attribution t_s
    assert end == 30.0


# --- merge-back ------------------------------------------------------------

def test_resolved_ambiguity_appends_inferred_clause_not_a_new_step():
    doc = _two_step_doc(
        flags=[Flag(flag_id="FLAG-01", family="ambiguity", question="q", severity="blocking", window_segment_ids=[0], step_id="step-01")]
    )
    flag = doc.flags[0]
    output = CheckerOutput(verdict="resolved", inferred_text="the reference sheet, tab 2")
    result = _apply_resolution(doc, flag, output, [Path("t00001000.png")])

    assert len(result.steps) == 2  # no new step for an ambiguity resolution
    step1 = next(s for s in result.steps if s.step_id == "step-01")
    assert len(step1.inferred_clauses) == 1
    assert step1.inferred_clauses[0].text == "the reference sheet, tab 2"
    assert step1.instruction == "Open the sheet"  # byte-stable, augmented not rewritten
    assert result.flags[0].status == "resolved"
    assert result.resolutions[0].verdict == "resolved"


def test_resolved_structural_inserts_new_step_and_renumbers_order():
    doc = _two_step_doc(
        flags=[Flag(flag_id="FLAG-01", family="structural", question="q", severity="blocking", window_segment_ids=[0], between_step_ids=("step-01", "step-02"))]
    )
    flag = doc.flags[0]
    output = CheckerOutput(verdict="resolved", inferred_text="Switch to the target sheet")
    result = _apply_resolution(doc, flag, output, [Path("t00010000.png")])

    assert len(result.steps) == 3
    ordered = sorted(result.steps, key=lambda s: s.order)
    assert [s.order for s in ordered] == [1, 2, 3]
    inserted = ordered[1]
    assert inserted.step_id not in ("step-01", "step-02")
    assert inserted.instruction == "Switch to the target sheet"
    assert inserted.attributions[0].kind == "inferred"
    assert result.flags[0].status == "resolved"


def test_contradicted_forces_severity_blocking_and_records_both_versions():
    doc = _two_step_doc(
        flags=[Flag(flag_id="FLAG-01", family="ambiguity", question="q", severity="note", window_segment_ids=[0], step_id="step-01")]
    )
    flag = doc.flags[0]
    output = CheckerOutput(
        verdict="contradicted", narration_version="updates automatically", screen_version="no change shown after paste"
    )
    result = _apply_resolution(doc, flag, output, [Path("t00001000.png")])

    assert result.flags[0].status == "contradicted"
    assert result.flags[0].severity == "blocking"
    assert result.resolutions[0].narration_version == "updates automatically"
    assert result.resolutions[0].screen_version == "no change shown after paste"


def test_unresolvable_flag_gets_distinct_status_not_reused_open():
    """A checked-but-unresolvable flag must be distinguishable from one that was never checked --
    both would render identically as status "open" otherwise, which breaks Phase 4's escalation
    queue (it can't tell "we don't know" from "we tried and still don't know")."""
    doc = _two_step_doc(
        flags=[Flag(flag_id="FLAG-01", family="ambiguity", question="q", severity="blocking", window_segment_ids=[0], step_id="step-01")]
    )
    flag = doc.flags[0]
    output = CheckerOutput(verdict="unresolvable", reason="nothing on screen shows the join key")
    result = _apply_resolution(doc, flag, output, [])

    assert result.flags[0].status == "unresolvable"
    assert result.resolutions[0].evidence == []
    assert result.resolutions[0].reason == "nothing on screen shows the join key"


def test_unresolvable_flag_gets_default_reason_if_model_omitted_one():
    """Provenance requires a non-empty reason for unresolvable -- a defensive fallback so a model
    that forgets 'reason' doesn't crash the merge with a ValidationError."""
    doc = _two_step_doc(
        flags=[Flag(flag_id="FLAG-01", family="ambiguity", question="q", severity="blocking", window_segment_ids=[0], step_id="step-01")]
    )
    flag = doc.flags[0]
    output = CheckerOutput(verdict="unresolvable", reason="")
    result = _apply_resolution(doc, flag, output, [])
    assert result.resolutions[0].reason != ""
