"""Investigation index (three triggers, merged) and coverage accounting. Synthetic events, flags and
records: no video, no OCR, no API."""
from __future__ import annotations

from typing import List

from core.models.screen import ChangeRecord, ScreenEvent, ScreenTimeline, SpeechClue
from core.models.sop import Attribution, Flag, ObservedChange, SOPDocument, SOPStep
from core.models.tokens import TranscriptSegment
from core.screen.investigate import build_investigations, coverage_of, explained_event_ids, update_status
from core.screen.surfaces import diff_locators, locate
from report.sop_render import render_sop_md

BUDGET = "Budget.xlsx | C7 | fx | 4500 | Sheet 1 of 3 | PageStyle_Budget"
ACTUALS = "Budget.xlsx | A1 | fx | Vendor | Sheet 2 of 3 | PageStyle_Actuals"
TRANSCRIPT = [TranscriptSegment(start_s=6.0 * i, end_s=6.0 * (i + 1), text=f"seg {i}") for i in range(4)]


def _ev(n: int, t: float, etype="local", ctx="ctx-001", transient=False) -> ScreenEvent:
    return ScreenEvent(
        event_id=f"ev-{n:03d}", t_start=t, t_end=t + 0.3, type=etype, changed_pct=1.0, coverage_pct=1.0,
        before_frame="b.png", after_frame="a.png", context_id=ctx, transient=transient,
    )


def _rec(n: int, t: float, before=BUDGET, after=BUDGET, clue=None) -> ChangeRecord:
    lb, la = locate(before), locate(after)
    return ChangeRecord(
        event_id=f"ev-{n:03d}", t_start=t, t_end=t + 0.3, type="local", surface_before=lb, surface_after=la,
        surface_change=diff_locators(lb, la), speech_clue=clue,
    )


def _doc(flags=(), observed=()) -> SOPDocument:
    steps = []
    for n in (1, 2):
        step = SOPStep(
            step_id=f"step-{n:02d}", order=n, title=f"t{n}", instruction=f"Do {n}.",
            attributions=[Attribution(kind="said", t_s=6.0 * n, segment_ids=[n])],
        )
        if n == 1 and observed:
            step = step.model_copy(update={"observed_changes": [ObservedChange(text="x", event_ids=list(observed), t_s=1.0)]})
        steps.append(step)
    return SOPDocument(run_id="r", steps=steps, flags=list(flags))


def _flag(n: int, *, origin="reviewer", family="ambiguity", events=(), window=None, status="open") -> Flag:
    return Flag(
        flag_id=f"FLAG-{n:02d}", family=family, question=f"question {n}", severity="caution", origin=origin,
        event_ids=list(events), window_time_range=window, status=status,
    )


def _timeline(events: List[ScreenEvent]) -> ScreenTimeline:
    return ScreenTimeline(video="v", duration_s=30.0, width=40, height=20, events=events)


def _kinds(inv) -> List[str]:
    return sorted({t.kind for t in inv.triggers})


def test_unexplained_sheet_switch_is_one_investigation_with_two_triggers():
    tl = _timeline([_ev(1, 2.0, "scene_cut", "ctx-002")])
    invs = build_investigations(tl, [_rec(1, 2.0, BUDGET, ACTUALS)], _doc(), TRANSCRIPT, explained=set())
    assert len(invs) == 1 and invs[0].event_ids == ["ev-001"]
    assert _kinds(invs[0]) == ["A_context", "C_unexplained"]
    assert "sheet_switch to Actuals" in next(t.detail for t in invs[0].triggers if t.kind == "A_context")


def test_unnarrated_edit_during_unrelated_speech_is_found_by_trigger_c_alone():
    tl = _timeline([_ev(1, 3.0)])
    invs = build_investigations(tl, [_rec(1, 3.0)], _doc(), TRANSCRIPT, explained=set())
    assert len(invs) == 1 and _kinds(invs[0]) == ["C_unexplained"]


def test_speech_clue_is_reported_on_the_context_trigger():
    clue = SpeechClue(found=False, nav_words=["click"])
    tl = _timeline([_ev(1, 2.0, "scene_cut", "ctx-002")])
    invs = build_investigations(tl, [_rec(1, 2.0, BUDGET, ACTUALS, clue)], _doc(), TRANSCRIPT, explained={"ev-001"})
    assert "not named in speech" in invs[0].triggers[0].detail
    assert _kinds(invs[0]) == ["A_context"]  # explained -> no Trigger C


def test_transcript_flag_sharing_an_event_merges_into_one_investigation():
    tl = _timeline([_ev(1, 2.0, "scene_cut", "ctx-002")])
    flag = _flag(1, events=["ev-001"], window=(5.0, 9.0))  # narrated after the action
    invs = build_investigations(tl, [_rec(1, 2.0, BUDGET, ACTUALS)], _doc([flag]), TRANSCRIPT, explained={"ev-001"})
    assert len(invs) == 1
    assert _kinds(invs[0]) == ["A_context", "B_transcript"] and invs[0].flag_ids == ["FLAG-01"]
    assert invs[0].t_start == 2.0 and invs[0].t_end == 2.3  # the screen stretch, not the narration window


def test_a_flag_attached_to_distant_events_does_not_bridge_them_into_one_investigation():
    tl = _timeline([_ev(1, 2.0), _ev(2, 15.0), _ev(3, 25.0)])
    flag = _flag(1, events=["ev-001", "ev-002", "ev-003"], window=(0.0, 30.0))
    invs = build_investigations(tl, [_rec(1, 2.0), _rec(2, 15.0), _rec(3, 25.0)], _doc([flag]), TRANSCRIPT, explained={"ev-001", "ev-002", "ev-003"})
    assert [v.event_ids for v in invs] == [["ev-001"], ["ev-002"], ["ev-003"]]
    assert all(v.flag_ids == ["FLAG-01"] and _kinds(v) == ["B_transcript"] for v in invs)


def test_overlapping_a_b_c_share_one_investigation_with_three_reasons():
    tl = _timeline([_ev(1, 2.0, "scene_cut", "ctx-002")])
    flag = _flag(1, events=["ev-001"], window=(5.0, 9.0))
    invs = build_investigations(tl, [_rec(1, 2.0, BUDGET, ACTUALS)], _doc([flag]), TRANSCRIPT, explained=set())
    assert len(invs) == 1 and _kinds(invs[0]) == ["A_context", "B_transcript", "C_unexplained"]


def test_silent_step_flag_attaches_to_trigger_c_without_adding_a_trigger():
    tl = _timeline([_ev(1, 3.0)])
    silent = _flag(1, origin="reconcile", family="structural", events=["ev-001"], window=(0.0, 6.0))
    invs = build_investigations(tl, [_rec(1, 3.0)], _doc([silent]), TRANSCRIPT, explained=set())
    assert len(invs) == 1 and _kinds(invs[0]) == ["C_unexplained"] and invs[0].flag_ids == ["FLAG-01"]


def test_transcript_flag_with_no_screen_change_is_kept_and_not_dropped():
    tl = _timeline([_ev(1, 2.0)])
    flag = _flag(1, window=(20.0, 24.0))
    invs = build_investigations(tl, [_rec(1, 2.0)], _doc([flag]), TRANSCRIPT, explained={"ev-001"})
    standalone = [v for v in invs if v.event_ids == []]
    assert len(standalone) == 1 and standalone[0].flag_ids == ["FLAG-01"] and _kinds(standalone[0]) == ["B_transcript"]


def test_eventless_flag_joins_an_overlapping_investigation_in_the_same_context():
    tl = _timeline([_ev(1, 2.0)])
    flag = _flag(1, window=(1.0, 4.0))
    invs = build_investigations(tl, [_rec(1, 2.0)], _doc([flag]), TRANSCRIPT, explained=set())
    assert len(invs) == 1 and _kinds(invs[0]) == ["B_transcript", "C_unexplained"]


def test_unrelated_events_stay_separate_and_ids_follow_time():
    tl = _timeline([_ev(2, 15.0), _ev(1, 2.0)])
    invs = build_investigations(tl, [_rec(1, 2.0), _rec(2, 15.0)], _doc(), TRANSCRIPT, explained=set())
    assert [v.inv_id for v in invs] == ["inv-001", "inv-002"] and invs[0].event_ids == ["ev-001"]


def test_dropdown_opened_and_closed_is_labelled_as_an_interaction_not_a_change():
    tl = _timeline([_ev(1, 3.0, transient=True)])
    invs = build_investigations(tl, [_rec(1, 3.0)], _doc(), TRANSCRIPT, explained=set())
    assert "no committed change" in invs[0].triggers[0].detail


def test_explained_events_come_from_observed_changes_and_align_buckets():
    doc = _doc(observed=["ev-001"])
    assert explained_event_ids(doc, [{"event_id": "ev-002", "bucket": "explained"}, {"event_id": "ev-003", "bucket": "silent"}]) == {
        "ev-001", "ev-002",
    }


# --- status and coverage ------------------------------------------------------------


def _status_case(flag_status, *, budget=False, explained=()):
    tl = _timeline([_ev(1, 3.0)])
    flag = _flag(1, origin="reconcile", family="structural", events=["ev-001"], window=(0.0, 6.0), status=flag_status)
    doc = _doc([flag])
    invs = build_investigations(tl, [_rec(1, 3.0)], doc, TRANSCRIPT, explained=set(explained))
    return update_status(invs, doc, set(explained), budget_exceeded=budget)[0].status


def test_status_examined_when_the_checker_gave_a_verdict():
    assert _status_case("resolved") == "examined" and _status_case("unresolvable") == "examined"


def test_status_skipped_budget_vs_skipped_cap():
    assert _status_case("unchecked", budget=True) == "skipped_budget"
    assert _status_case("unchecked", budget=False) == "skipped_cap"


def test_status_unexamined_when_nothing_reached_it():
    assert _status_case("open") == "unexamined" and _status_case("check_failed") == "unexamined"


def test_status_examined_when_a_grounded_observation_covers_the_event():
    tl = _timeline([_ev(1, 2.0, "scene_cut", "ctx-002")])
    doc = _doc(observed=["ev-001"])
    invs = build_investigations(tl, [_rec(1, 2.0, BUDGET, ACTUALS)], doc, TRANSCRIPT, explained={"ev-001"})
    assert update_status(invs, doc, {"ev-001"})[0].status == "examined"


def test_an_explained_plain_edit_needs_no_investigation():
    tl = _timeline([_ev(1, 3.0)])
    assert build_investigations(tl, [_rec(1, 3.0)], _doc(), TRANSCRIPT, explained={"ev-001"}) == []


def test_coverage_counts_and_lists_the_spans_not_examined():
    tl = _timeline([_ev(1, 3.0), _ev(2, 15.0)])
    doc = _doc([_flag(1, origin="reconcile", family="structural", events=["ev-001"], window=(0, 6), status="resolved")])
    invs = update_status(build_investigations(tl, [_rec(1, 3.0), _rec(2, 15.0)], doc, TRANSCRIPT, set()), doc, set())
    cov = coverage_of(invs)
    assert (cov.total, cov.examined, cov.unexamined) == (2, 1, 1) and cov.not_examined_spans == [(15.0, 15.3)]


def test_report_does_not_claim_nothing_is_missing_when_screen_was_not_fully_examined():
    tl = _timeline([_ev(1, 15.0)])
    doc = _doc()
    cov = coverage_of(update_status(build_investigations(tl, [_rec(1, 15.0)], doc, TRANSCRIPT, set()), doc, set()))
    md = render_sop_md(doc.model_copy(update={"screen_coverage": cov}), video_name="v.mp4")
    assert "Screen coverage" in md and "not examined" in md
    assert "Nothing is missing" not in md and "not evidence nothing is missing" in md


def test_report_keeps_the_original_line_when_coverage_is_complete_or_absent():
    assert "Nothing is missing" in render_sop_md(_doc(), video_name="v.mp4")
    full = _doc().model_copy(update={"screen_coverage": coverage_of([])})
    assert "Nothing is missing" in render_sop_md(full, video_name="v.mp4")
