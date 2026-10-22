"""Steps that were done on screen but never said (docs/26 flow plan, phase 1d). Synthetic records and a fake
client: no video, no OCR, no API."""
from __future__ import annotations

import ast

import pytest

from core.models.reconcile import EventAssignment, QuotedEvidence, ReconcilerOutput
from core.models.screen import ChangeRecord, RegionChange, SelectionAnchor, UnusedEvent
from core.models.sop import Attribution, SOPDocument, SOPStep
from core.screen.reconcile import build_reconciler_input, merge_reconciler_output
from report.sop_render import render_sop_md
from tests.test_reconcile import RECORDS, TRANSCRIPT, _doc


def _unowned(n, t, event_class="data_change", before="", after="Status Paid") -> ChangeRecord:
    return ChangeRecord(
        event_id="ev-%03d" % n, t_start=t, t_end=t + 0.3, type="local", event_class=event_class,
        regions=[RegionChange(box=(0, 0, 10, 10), before=before, after=after)],
    )


def _assign(event_id, action="", quotes=()):
    return EventAssignment(
        event_id=event_id, step_id=None, action=action,
        evidence=[QuotedEvidence(event_id=event_id, quote=q) for q in quotes],
    )


OWNED = [
    EventAssignment(event_id="ev-001", step_id="step-01"),
    EventAssignment(event_id="ev-002", step_id="step-02"),
    EventAssignment(event_id="ev-003", step_id="step-03"),
]


def _merge(extra_records, assignments):
    out = ReconcilerOutput(events=OWNED + list(assignments))
    return merge_reconciler_output(_doc(), out, list(RECORDS) + extra_records, TRANSCRIPT)


def test_a_grounded_unnarrated_change_becomes_a_shown_step_placed_by_time():
    doc = _merge([_unowned(4, 9.0)], [_assign("ev-004", "A cell was set to Paid.", ["Status Paid"])])
    assert [s.step_id for s in doc.steps] == ["step-01", "step-02", "step-s01", "step-03"]
    assert [s.order for s in doc.steps] == [1, 2, 3, 4]
    new = doc.steps[2]
    assert new.attributions[0].kind == "shown" and new.attributions[0].event_ids == ["ev-004"]
    assert new.observed_changes[0].quotes == ["Status Paid"] and doc.provenance_complete
    assert not [f for f in doc.flags if f.event_ids == ["ev-004"]]  # the silent-change flag is not also raised


def test_an_action_with_no_verbatim_quote_stays_a_silent_change_flag():
    doc = _merge([_unowned(4, 9.0)], [_assign("ev-004", "A cell was set to Paid.", ["Status Approved"])])
    assert len(doc.steps) == 3
    flag = [f for f in doc.flags if f.event_ids == ["ev-004"]][0]
    assert flag.family == "structural" and flag.finding_kind == "observed_unexplained"


def test_a_quote_taken_from_another_event_does_not_ground_the_action():
    wrong = _assign("ev-004", "A cell was set.", [])
    wrong.evidence = [QuotedEvidence(event_id="ev-002", quote="45.000")]
    assert len(_merge([_unowned(4, 9.0)], [wrong]).steps) == 3


def test_only_navigation_and_data_change_become_steps():
    for cls in ("selection", "unclear", "transient"):
        doc = _merge([_unowned(4, 9.0, event_class=cls)], [_assign("ev-004", "Something happened.", ["Status Paid"])])
        assert len(doc.steps) == 3, cls


def test_no_action_means_no_step():
    assert len(_merge([_unowned(4, 9.0)], [_assign("ev-004", "", [])]).steps) == 3


def test_several_shown_steps_get_distinct_ids_and_are_placed_by_time():
    doc = _merge(
        [_unowned(4, 0.2, after="Open file"), _unowned(5, 9.0)],
        [_assign("ev-004", "A file was opened.", ["Open file"]), _assign("ev-005", "A cell was set to Paid.", ["Status Paid"])],
    )
    ids = [s.step_id for s in doc.steps]
    assert ids == ["step-01", "step-s01", "step-02", "step-s02", "step-03"]  # placed after the step that had begun
    assert len({s.step_id for s in doc.steps}) == len(doc.steps)


def test_nothing_is_renumbered_when_no_step_is_shown():
    doc = _merge([], [])
    assert [s.order for s in doc.steps] == [1, 2, 3]


def test_a_shown_attribution_without_events_breaks_provenance():
    with pytest.raises(ValueError, match="shown attribution"):
        SOPDocument(run_id="r", steps=[SOPStep(step_id="s", order=1, title="t", instruction="i",
                                               attributions=[Attribution(kind="shown", t_s=1.0)])])


def test_the_report_marks_a_shown_step_and_draws_it_solid():
    md = render_sop_md(_merge([_unowned(4, 9.0)], [_assign("ev-004", "A cell was set to Paid.", ["Status Paid"])]), video_name="v.mp4")
    assert "(done on screen, not said)" in md and "`[shown @0:09, ev-004]`" in md
    assert "-->|shown|" in md  # no dashed 'gap' mark where a shown step closes the gap


def test_the_reconciler_input_carries_the_event_class_and_the_anchor():
    anchor = SelectionAnchor(event_id="ev-004", t_s=9.0, focus="C2", role="pointer", confirmed=False)
    event = ast.literal_eval(build_reconciler_input(_doc(), [_unowned(4, 9.0)], [], [anchor]))["events"][0]
    assert event["event_class"] == "data_change" and event["anchor"] == {"cell": "C2", "role": "pointer", "confirmed": False}


def test_the_report_lists_linked_selections_and_activity_that_became_no_step():
    doc = _merge([], [])
    doc = doc.model_copy(update={
        "anchors": [SelectionAnchor(event_id="ev-001", t_s=3.0, focus="C2", content="45000", role="pointer", signals=["speech:pointer:this"])],
        "screen_activity_unused": [UnusedEvent(event_id="ev-002", t_s=5.0, event_class="transient", reason="opened and closed")],
    })
    md = render_sop_md(doc, video_name="v.mp4")
    assert "possibly C2" in md and "speech:pointer:this" in md
    assert "transient -- opened and closed `[ev-002]`" in md
