"""Unit tests for core/screen/align.py -- silent-step candidates and evidence attachment from
synthetic steps, transcript and events. No video, no API."""
from __future__ import annotations

from typing import List

from core.models.screen import ScreenEvent
from core.models.sop import Attribution, Flag, SOPDocument, SOPStep
from core.models.tokens import TranscriptSegment
from core.screen.align import align_events

# Four 6-second segments, as long as real ones: a +/-3 s window would match nearly any event.
TRANSCRIPT = [TranscriptSegment(start_s=6.0 * i, end_s=6.0 * (i + 1), text=f"seg {i}") for i in range(4)]


def _step(n: int, seg: int) -> SOPStep:
    return SOPStep(
        step_id=f"step-{n:02d}",
        order=n,
        title=f"Step {n}",
        instruction=f"Do thing {n}.",
        attributions=[Attribution(kind="said", t_s=6.0 * seg, segment_ids=[seg])],
    )


def _doc(flags: List[Flag] | None = None) -> SOPDocument:
    return SOPDocument(run_id="r", steps=[_step(1, 0), _step(2, 2)], flags=flags or [])


def _ev(n: int, t: float, typ: str = "local") -> ScreenEvent:
    return ScreenEvent(
        event_id=f"ev-{n:03d}", t_start=t, t_end=t + 0.3, type=typ, changed_pct=1.0, coverage_pct=5.0,
        before_frame="b.png", after_frame="a.png",
    )


def test_event_inside_a_step_span_is_explained():
    res = align_events(_doc(), [_ev(1, 3.0)], TRANSCRIPT)
    assert [f for f in res.doc.flags if f.origin == "screen"] == []
    assert res.records[0]["bucket"] == "explained" and res.records[0]["step_ids"] == ["step-01"]


def test_event_in_a_gap_no_step_covers_becomes_a_screen_flag():
    # step-01 spans 0-6, step-02 spans 12-18; 8.0 is 4 s before step-02: outside the 3 s lead.
    res = align_events(_doc(), [_ev(1, 8.0, "scene_cut")], TRANSCRIPT)
    silent = [f for f in res.doc.flags if f.origin == "screen"]
    assert len(silent) == 1
    f = silent[0]
    assert f.family == "structural" and f.between_step_ids == ("step-01", "step-02")
    assert f.event_ids == ["ev-001"] and f.window_time_range is not None
    assert res.records[0]["bucket"] == "silent"


def test_action_just_before_its_narration_is_not_silent():
    res = align_events(_doc(), [_ev(1, 10.0)], TRANSCRIPT)  # 2 s before step-02's span starts
    assert [f for f in res.doc.flags if f.origin == "screen"] == []
    assert res.records[0]["step_ids"] == ["step-02"]


def test_surplus_events_inside_one_span_are_candidates():
    res = align_events(_doc(), [_ev(1, 1.0), _ev(2, 2.0), _ev(3, 4.0)], TRANSCRIPT)
    silent = [f for f in res.doc.flags if f.origin == "screen"]
    assert [f.event_ids for f in silent] == [["ev-003"]]  # the first two fit the allowance


def test_existing_flag_for_the_same_pair_suppresses_a_duplicate():
    existing = Flag(
        flag_id="FLAG-01", family="structural", question="q", severity="blocking",
        window_segment_ids=[0, 2], between_step_ids=("step-01", "step-02"), origin="coverage",
    )
    res = align_events(_doc([existing]), [_ev(1, 8.0)], TRANSCRIPT)
    assert [f for f in res.doc.flags if f.origin == "screen"] == []
    assert res.doc.flags[0].event_ids == ["ev-001"]  # but it still gets the event as evidence


def test_events_are_attached_to_flags_whose_window_they_touch():
    flag = Flag(flag_id="FLAG-01", family="ambiguity", question="q", severity="blocking",
                window_segment_ids=[2], step_id="step-02")
    res = align_events(_doc([flag]), [_ev(1, 1.0), _ev(2, 13.0), _ev(3, 40.0)], TRANSCRIPT)
    assert res.doc.flags[0].event_ids == ["ev-002"]
    assert next(r for r in res.records if r["event_id"] == "ev-002")["bucket"] == "vague"


def test_steps_with_no_nearby_event_are_reported():
    res = align_events(_doc(), [_ev(1, 3.0)], TRANSCRIPT)
    assert res.said_no_event_step_ids == ["step-02"]


def test_no_events_changes_nothing():
    doc = _doc()
    res = align_events(doc, [], TRANSCRIPT)
    assert res.doc.flags == doc.flags and res.records == []


def test_event_before_the_first_step_gets_a_single_step_anchor():
    doc = SOPDocument(run_id="r", steps=[_step(1, 2)])
    res = align_events(doc, [_ev(1, 1.0)], TRANSCRIPT)  # 11 s before the only step
    silent = [f for f in res.doc.flags if f.origin == "screen"]
    assert len(silent) == 1 and silent[0].between_step_ids is None and silent[0].step_id == "step-01"


def test_without_silent_flags_events_attach_to_existing_flags_but_create_none():
    """silent_flags=False is the mode used when the Reconciler owns silent-step detection."""
    gap_event = _ev(1, 8.0, "scene_cut")  # in a gap no step covers: normally a silent-step flag
    asked = Flag(flag_id="FLAG-01", family="structural", question="What happens between them?", severity="blocking",
                 window_time_range=(6.0, 12.0), between_step_ids=("step-01", "step-02"))
    res = align_events(_doc([asked]), [gap_event], TRANSCRIPT, silent_flags=False)
    assert [f.flag_id for f in res.doc.flags] == ["FLAG-01"]  # no new flag
    assert res.doc.flags[0].event_ids == ["ev-001"]  # but the event is attached as evidence to the flag that asked
    assert align_events(_doc(), [gap_event], TRANSCRIPT, silent_flags=True).doc.flags  # the default mode would have created one
