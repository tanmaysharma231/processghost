"""The four slots of a data step (source, key, target, effect): asked by omission, answered by speech, screen
or anchors. Synthetic documents and records: no video, no OCR, no API."""
from __future__ import annotations

import ast

from core.llm.parsing import merge_reviewer_output
from core.models.reconcile import EventAssignment, QuotedEvidence, ReconcilerOutput, SlotFillClaim, StepReconciliation
from core.models.screen import ChangeRecord, Locator, SelectionAnchor
from core.models.sop import ObservedChange, ReviewerOutput, ReviewerOutputFlag, ReviewerOutputStep, SlotClaim, SlotFill
from core.screen.reconcile import build_reconciler_input, merge_reconciler_output
from core.screen.slot_fills import fill_slots_from_anchors
from core.slots import add_slot_flags, applicable_slots, fill_slot, open_slots
from tests.test_reconcile import RAW, RECORDS, TRANSCRIPT, _doc
from tests.test_shown_steps import OWNED, _assign, _unowned

SEGMENTS = [
    {"start": 0.0, "end": 6.0, "text": "go to the sheet"},
    {"start": 6.0, "end": 12.0, "text": "copy the amount from the transactions sheet and paste it into the actual column"},
    {"start": 12.0, "end": 18.0, "text": "the total updates"},
]


def _review(steps=(), flags=()):
    return merge_reviewer_output(ReviewerOutput(steps=list(steps), flags=list(flags)), _doc(), segments=SEGMENTS)


def _move(**slots):
    return ReviewerOutputStep(step_id="step-02", kind="move_data", **slots)


def _slot_flags(doc, step_id="step-02"):
    return {f.slot: f for f in doc.flags if f.step_id == step_id and f.slot}


# --- which slots apply ----------------------------------------------------------------


def test_slots_depend_on_the_step_kind():
    assert applicable_slots("move_data") == ("source", "key", "target", "effect")
    assert applicable_slots("edit") == ("key", "target", "effect")
    assert applicable_slots("navigate") == () and applicable_slots(None) == ()


# --- Reviewer: answered only with a verbatim quote, asked by omission ----------------------


def test_a_quoted_answer_fills_the_slot_and_every_other_applicable_slot_becomes_a_question():
    doc = _review([_move(source=SlotClaim(value="the transactions sheet", quote="from the transactions sheet"),
                         target=SlotClaim(value="the actual column", quote="into the actual column"))])
    step = doc.steps[1]
    assert step.kind == "move_data" and set(step.slots) == {"source", "target"}
    assert step.slots["source"].by == "said" and step.slots["source"].quote == "from the transactions sheet"
    flags = _slot_flags(doc)
    assert set(flags) == {"key", "effect"} and flags["key"].severity == "blocking" and flags["effect"].severity == "caution"
    assert "How is the right row or item picked" in flags["key"].question and doc.provenance_complete


def test_a_claim_with_a_quote_that_is_not_in_the_transcript_leaves_the_slot_open():
    doc = _review([_move(key=SlotClaim(value="by Bill ID", quote="matched by the bill identifier"))])
    assert "key" not in doc.steps[1].slots and "key" in _slot_flags(doc)


def test_a_claim_without_a_quote_or_a_value_is_no_answer():
    doc = _review([_move(key=SlotClaim(value="by id", quote=""), target=SlotClaim(value="", quote="the actual column"))])
    assert set(_slot_flags(doc)) == {"source", "key", "target", "effect"}


def test_a_navigation_step_is_never_asked_for_slots():
    doc = _review([ReviewerOutputStep(step_id="step-01", kind="navigate")])
    assert _slot_flags(doc, "step-01") == {} and doc.steps[0].kind == "navigate"


def test_a_step_the_reviewer_did_not_classify_gets_no_slot_questions():
    doc = _review([ReviewerOutputStep(step_id="step-01", kind=None), _move()])
    assert {f.step_id for f in doc.flags if f.slot} == {"step-02"}  # only the classified data step is asked


def test_a_question_the_reviewer_already_asked_about_a_slot_is_not_asked_twice():
    asked = ReviewerOutputFlag(family="ambiguity", question="Which column does it go into?", severity="blocking",
                               step_id="step-02", slot="target", window_segment_ids=[1])
    doc = _review([_move()], [asked])
    assert [f.question for f in doc.flags if f.slot == "target"] == ["Which column does it go into?"]
    assert set(_slot_flags(doc)) == {"source", "key", "target", "effect"}


def test_adding_slot_flags_twice_does_not_duplicate_questions():
    doc = _review([_move()])
    assert len(add_slot_flags(doc).flags) == len(doc.flags)


# --- filling a slot resolves its question ----------------------------------------------


def test_filling_a_slot_resolves_the_flag_with_the_events_that_answered_it():
    doc = _review([_move()])
    done = fill_slot(doc, "step-02", "target", SlotFill(value="F18", by="shown", event_ids=["ev-002"]))
    flag = _slot_flags(done)["target"]
    res = [r for r in done.resolutions if r.flag_id == flag.flag_id][0]
    assert flag.status == "resolved" and res.event_ids == ["ev-002"] and res.inferred_text == "F18"
    assert done.steps[1].slots["target"].by == "shown" and done.provenance_complete


def test_an_existing_answer_is_never_overwritten_and_inapplicable_slots_are_refused():
    doc = fill_slot(_review([_move()]), "step-02", "target", SlotFill(value="first", by="shown", event_ids=["ev-001"]))
    again = fill_slot(doc, "step-02", "target", SlotFill(value="second", by="shown", event_ids=["ev-002"]))
    assert again.steps[1].slots["target"].value == "first"
    nav = fill_slot(_review([ReviewerOutputStep(step_id="step-01", kind="navigate")]), "step-01", "key", SlotFill(value="x", by="shown"))
    assert nav.steps[0].slots == {}


# --- Reconciler: slot fills grounded in the records ---------------------------------------


def _reconcile(claims):
    out = ReconcilerOutput(
        steps=[StepReconciliation(step_id="step-02", event_ids=["ev-002"], slot_fills=claims)], events=OWNED,
    )
    return merge_reconciler_output(_review([_move()]), out, RECORDS, TRANSCRIPT)


def test_a_slot_fill_with_a_verbatim_record_quote_answers_the_question():
    doc = _reconcile([SlotFillClaim(slot="target", value="F18", evidence=[QuotedEvidence(event_id="ev-002", quote="F18")])])
    assert doc.steps[1].slots["target"].event_ids == ["ev-002"] and _slot_flags(doc)["target"].status == "resolved"


def test_a_slot_fill_with_an_invented_quote_is_rejected():
    doc = _reconcile([SlotFillClaim(slot="target", value="F18", evidence=[QuotedEvidence(event_id="ev-002", quote="Z99")])])
    assert "target" not in doc.steps[1].slots and _slot_flags(doc)["target"].status == "open"


def test_a_slot_fill_for_a_slot_that_is_not_open_is_ignored():
    doc = _reconcile([SlotFillClaim(slot="key", value="F18", evidence=[QuotedEvidence(event_id="ev-002", quote="F18")])])
    assert doc.steps[1].slots["key"].by == "shown"  # open, so accepted
    again = merge_reconciler_output(
        doc, ReconcilerOutput(steps=[StepReconciliation(step_id="step-02", event_ids=["ev-002"], slot_fills=[
            SlotFillClaim(slot="key", value="other", evidence=[QuotedEvidence(event_id="ev-002", quote="F18")])])], events=OWNED),
        RECORDS, TRANSCRIPT,
    )
    assert again.steps[1].slots["key"].value == "F18"


def test_the_reconciler_is_told_each_steps_kind_and_open_slots():
    payload = ast.literal_eval(build_reconciler_input(_review([_move(source=SlotClaim(value="x", quote="from the transactions sheet"))]), RECORDS, RAW))
    step = next(s for s in payload["steps"] if s["step_id"] == "step-02")
    assert step["kind"] == "move_data" and step["open_slots"] == ["key", "target", "effect"]


# --- shown steps and anchors ------------------------------------------------------------------


def test_an_edit_known_only_from_the_screen_gets_its_target_and_is_asked_the_rest():
    rec = _unowned(4, 9.0).model_copy(update={"surface_after": Locator(surface="spreadsheet", focus="H18")})
    out = ReconcilerOutput(events=OWNED + [_assign("ev-004", "A cell was set to Paid.", ["Status Paid"])])
    doc = merge_reconciler_output(_doc(), out, list(RECORDS) + [rec], TRANSCRIPT)
    shown = [s for s in doc.steps if s.step_id == "step-s01"][0]
    assert shown.kind == "edit" and shown.slots["target"].value == "H18" and open_slots(shown) == ["key", "effect"]
    flags = _slot_flags(doc, "step-s01")
    assert set(flags) == {"key", "effect"} and flags["key"].window_time_range == (9.0, 10.0) and doc.provenance_complete


def _anchor(confirmed=True, role="source"):
    return SelectionAnchor(event_id="ev-001", t_s=1.0, focus="C2", content="45000", role=role, confirmed=confirmed,
                           target_event_id="ev-002", signals=["speech:pointer:this", "value:ev-002"])


def _doc_with_observed_paste():
    doc = _review([_move()])
    steps = [s.model_copy(update={"observed_changes": [ObservedChange(text="pasted", event_ids=["ev-002"], t_s=7.0)]}) if s.step_id == "step-02" else s
             for s in doc.steps]
    return doc.model_copy(update={"steps": steps})


def test_a_confirmed_copy_paste_anchor_answers_source_and_target_without_a_model():
    landed = ChangeRecord(event_id="ev-002", t_start=7.0, t_end=7.3, type="local", surface_after=Locator(surface="spreadsheet", focus="F18"))
    doc = fill_slots_from_anchors(_doc_with_observed_paste(), [_anchor()], [landed])
    step = doc.steps[1]
    assert step.slots["source"].value == "C2 (value 45000)" and step.slots["target"].value == "F18"
    assert {s: f.status for s, f in _slot_flags(doc).items()} == {"source": "resolved", "key": "open", "target": "resolved", "effect": "open"}


def test_an_unconfirmed_anchor_fills_nothing():
    doc = fill_slots_from_anchors(_doc_with_observed_paste(), [_anchor(confirmed=False)], [])
    assert doc.steps[1].slots == {}


def test_a_confirmed_anchor_upgrades_an_unclassified_edit_to_a_move():
    base = _doc()
    steps = [s.model_copy(update={"observed_changes": [ObservedChange(text="x", event_ids=["ev-002"], t_s=7.0)]}) if s.step_id == "step-02" else s
             for s in base.steps]
    doc = fill_slots_from_anchors(base.model_copy(update={"steps": steps}), [_anchor()], [])
    assert doc.steps[1].kind == "move_data" and doc.steps[1].slots["source"].by == "shown"
    assert {f.slot for f in doc.flags if f.step_id == "step-02"} == {"key", "target", "effect"}  # target unread, so still asked
