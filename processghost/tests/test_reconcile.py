"""Unit tests for the Reconciler (core/screen/reconcile.py, records.py, the pipeline stage and the
word attachment). A fake client and synthetic records: no video, no OCR engine, no API."""
from __future__ import annotations

import ast

import pytest

from core import config
from core import pipeline
from core.llm import cache
from core.models.reconcile import (
    ClaimCheck, EventAssignment, QuotedEvidence, ReconcilerOutput, StepReconciliation, UnspecifiedQuestion,
)
from core.models.screen import ChangeRecord, RegionChange
from core.models.sop import Attribution, SOPDocument, SOPStep
from core.models.tokens import TranscriptSegment
from core.screen.reconcile import build_reconciler_input, merge_reconciler_output
from core.screen.records import build_change_records
from report.sop_render import render_sop_md
from tests.fakes import _FakeClient
from tests.test_checker_events import _timeline

TRANSCRIPT = [TranscriptSegment(start_s=6.0 * i, end_s=6.0 * (i + 1), text=f"seg {i}") for i in range(3)]
RAW = [
    {"start": 0.0, "end": 6.0, "text": "go to the sheet", "words": [{"word": " go", "start": 0.5, "end": 0.8}, {"word": " sheet", "start": 2.0, "end": 2.4}]},
    {"start": 6.0, "end": 12.0, "text": "paste it here"},
    {"start": 12.0, "end": 18.0, "text": "total updates"},
]


def _step(n, seg, title="t") -> SOPStep:
    return SOPStep(step_id=f"step-{n:02d}", order=n, title=f"{title}{n}", instruction=f"Do {n}.",
                   attributions=[Attribution(kind="said", t_s=6.0 * seg, segment_ids=[seg])])


def _doc() -> SOPDocument:
    return SOPDocument(run_id="r", steps=[_step(1, 0), _step(2, 1), _step(3, 2)])


def _rec(n, t, chrome_after="", before="", after="") -> ChangeRecord:
    return ChangeRecord(event_id=f"ev-{n:03d}", t_start=t, t_end=t + 0.3, type="local", chrome_after=chrome_after,
                        regions=[RegionChange(box=(0, 0, 10, 10), before=before, after=after)] if (before or after) else [])


RECORDS = [
    _rec(1, 1.0, chrome_after="Sheet2 | F18 | 45000"),
    _rec(2, 7.0, chrome_after="F18", before="500 | 4,100", after="500 | 45.000 | 4,100"),
    _rec(3, 13.0, chrome_after="Sum:45,000"),
]


def _out(**kw) -> ReconcilerOutput:
    return ReconcilerOutput(**kw)


def _step_rec(step_id, evs, what, quotes=(), unspecified=()):
    return StepReconciliation(
        step_id=step_id, event_ids=list(evs), what_changed=what,
        evidence=[QuotedEvidence(event_id=e, quote=q) for e, q in quotes],
        unspecified=[UnspecifiedQuestion(question=q, severity=s) for q, s in unspecified],
    )


def _flags(doc, **match):
    return [f for f in doc.flags if all(getattr(f, k) == v for k, v in match.items())]


# --- grounding ---------------------------------------------------------------------


def test_grounded_change_is_attached_to_the_step():
    out = _out(steps=[_step_rec("step-02", ["ev-002"], "A value 45.000 appeared.", [("ev-002", "45.000")])])
    doc = merge_reconciler_output(_doc(), out, RECORDS, TRANSCRIPT)
    oc = doc.steps[1].observed_changes
    assert len(oc) == 1 and oc[0].event_ids == ["ev-002"] and oc[0].t_s == 7.0 and oc[0].quotes == ["45.000"]


def test_quote_matching_tolerates_ocr_spacing():
    out = _out(steps=[_step_rec("step-02", ["ev-002"], "x", [("ev-002", "500 | 45.000")])])
    assert merge_reconciler_output(_doc(), out, RECORDS, TRANSCRIPT).steps[1].observed_changes


def test_invented_quote_drops_the_change_and_flags_said_not_shown():
    out = _out(steps=[_step_rec("step-02", ["ev-002"], "Total went from 0 to 235,000.", [("ev-002", "235,000")])])
    doc = merge_reconciler_output(_doc(), out, RECORDS, TRANSCRIPT)
    assert doc.steps[1].observed_changes == []
    flag = _flags(doc, origin="reconcile", step_id="step-02")[0]
    assert flag.status == "unresolvable" and "none of its quotes" in flag.question


def test_quote_from_the_wrong_event_does_not_ground():
    out = _out(steps=[_step_rec("step-01", ["ev-001"], "x", [("ev-002", "45.000")])])  # 45.000 is in ev-002, not ev-001
    assert merge_reconciler_output(_doc(), out, RECORDS, TRANSCRIPT).steps[0].observed_changes == []


def test_step_with_no_events_is_flagged_not_shown():
    out = _out(steps=[_step_rec("step-03", [], "")])
    flag = _flags(merge_reconciler_output(_doc(), out, RECORDS, TRANSCRIPT), origin="reconcile", step_id="step-03")[0]
    assert flag.family == "structural" and "no change on screen matches" in flag.question


# --- questions, claims, events -------------------------------------------------------


def test_unspecified_questions_become_unresolvable_flags_capped_and_deduped(monkeypatch):
    monkeypatch.setattr(config, "RECONCILER_MAX_QUESTIONS_PER_STEP", 2)
    qs = [("Why this row?", "blocking"), ("Why this row?", "blocking"), ("Which rule?", "caution"), ("Third?", "note")]
    out = _out(steps=[_step_rec("step-02", ["ev-002"], "x", [("ev-002", "45.000")], qs)])
    doc = merge_reconciler_output(_doc(), out, RECORDS, TRANSCRIPT)
    asked = _flags(doc, origin="reconcile", family="ambiguity")
    assert [f.question for f in asked] == ["Why this row?", "Which rule?"]  # capped at 2, duplicate dropped
    assert asked[0].severity == "blocking" and asked[0].status == "unresolvable" and asked[0].event_ids == ["ev-002"]


def test_unsupported_claim_is_a_blocking_flag():
    out = _out(claims=[ClaimCheck(step_id="step-03", claim="the total updates", supported="no")])
    flag = _flags(merge_reconciler_output(_doc(), out, RECORDS, TRANSCRIPT), origin="reconcile", family="ambiguity")[0]
    assert flag.severity == "blocking" and "the total updates" in flag.question and flag.status == "unresolvable"


def test_claim_marked_yes_needs_a_grounded_quote():
    bad = _out(claims=[ClaimCheck(step_id="step-03", claim="it updates", supported="yes",
                                  evidence=[QuotedEvidence(event_id="ev-003", quote="Total 235,000")])])
    flag = _flags(merge_reconciler_output(_doc(), bad, RECORDS, TRANSCRIPT), origin="reconcile", family="ambiguity")[0]
    assert flag.severity == "caution" and "clearly" in flag.question  # downgraded to unclear
    good = _out(claims=[ClaimCheck(step_id="step-03", claim="it updates", supported="yes",
                                   evidence=[QuotedEvidence(event_id="ev-003", quote="Sum:45,000")])])
    flags = _flags(merge_reconciler_output(_doc(), good, RECORDS, TRANSCRIPT), origin="reconcile", family="ambiguity")
    assert flags == []  # a supported, grounded claim raises no question


def test_event_no_step_explains_becomes_an_open_silent_flag():
    out = _out(events=[EventAssignment(event_id="ev-001", step_id="step-01"), EventAssignment(event_id="ev-002", step_id=None)])
    doc = merge_reconciler_output(_doc(), out, RECORDS, TRANSCRIPT)
    silent = _flags(doc, origin="reconcile", family="structural")
    ids = [f.event_ids for f in silent]
    assert ["ev-002"] in ids and ["ev-001"] not in ids
    assert all(f.status == "open" for f in silent)  # left open: the Checker can describe it


def test_unmentioned_events_are_silent_too():
    doc = merge_reconciler_output(_doc(), _out(), RECORDS, TRANSCRIPT)
    assert {tuple(f.event_ids) for f in _flags(doc, origin="reconcile")} == {("ev-001",), ("ev-002",), ("ev-003",)}


def test_unknown_step_ids_are_ignored():
    out = _out(steps=[_step_rec("step-99", ["ev-001"], "x", [("ev-001", "F18")])], claims=[ClaimCheck(step_id="step-99", claim="c", supported="no")])
    doc = merge_reconciler_output(_doc(), out, RECORDS, TRANSCRIPT)
    assert all(not s.observed_changes for s in doc.steps)
    assert _flags(doc, origin="reconcile", step_id="step-99") == []


# --- input, rendering, words, records, stage --------------------------------------------


def test_input_carries_word_times_and_falls_back_to_segment_times():
    payload = ast.literal_eval(build_reconciler_input(_doc(), RECORDS, RAW))
    spoken = {s["step_id"]: s["spoken"] for s in payload["steps"]}
    assert "go@0.5" in spoken["step-01"] and "sheet@2.0" in spoken["step-01"]  # word-timed where words exist
    assert spoken["step-02"] == "[6.0-12.0s] paste it here"  # segment-timed where they do not
    assert [e["event_id"] for e in payload["events"]] == ["ev-001", "ev-002", "ev-003"]


def test_observed_change_is_rendered_under_its_step():
    out = _out(steps=[_step_rec("step-02", ["ev-002"], "A value appeared.", [("ev-002", "45.000")])])
    md = render_sop_md(merge_reconciler_output(_doc(), out, RECORDS, TRANSCRIPT), video_name="v.mp4")
    assert "Observed on screen: A value appeared. `[observed @0:07, ev-002]`" in md


def test_attach_words_assigns_by_time_and_is_a_noop_without_words():
    segs = [{"start": 0.0, "end": 5.0, "text": "a"}, {"start": 5.0, "end": 10.0, "text": "b"}]
    pipeline._attach_words(segs, [{"word": "x", "start": 1.0, "end": 1.2}, {"word": "y", "start": 6.0, "end": 6.3}, {"word": "z", "start": 11.0, "end": 11.2}])
    assert [w["word"] for w in segs[0]["words"]] == ["x"] and [w["word"] for w in segs[1]["words"]] == ["y", "z"]
    plain = [{"start": 0.0, "end": 5.0, "text": "a"}]
    pipeline._attach_words(plain, [])
    assert "words" not in plain[0]


def test_change_records_cache_ocr_by_image_content(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    tl = _timeline(tmp_path, [2.0, 9.0])
    calls = []

    def ocr(img):
        calls.append(1)
        return "txt"

    first = build_change_records(tl, tmp_path, ocr)
    n = len(calls)
    assert len(first) == 2 and first[0].regions and "txt" in first[0].text()
    build_change_records(tl, tmp_path, ocr)
    assert len(calls) == n  # the second pass is served entirely from the OCR cache


def test_reconcile_stage_returns_none_and_records_failure_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "get_ocr", lambda: (lambda img: "txt"))
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")  # stub OCR must not enter the shared OCR cache
    tl = _timeline(tmp_path / "screen", [2.0])
    client = _FakeClient(structured_response=RuntimeError("model down"))
    assert pipeline._reconcile_stage(client, tmp_path, _doc(), tl, RAW, TRANSCRIPT) is None
    assert client.metrics.failures[-1]["stage"] == "reconcile_failed"


def test_reconcile_stage_without_ocr_engine_fails_soft(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "get_ocr", lambda: None)
    client = _FakeClient()
    assert pipeline._reconcile_stage(client, tmp_path, _doc(), _timeline(tmp_path / "screen", [2.0]), RAW, TRANSCRIPT) is None
    assert "OCR" in client.metrics.failures[-1]["error"]


def test_reconcile_stage_merges_a_good_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "get_ocr", lambda: (lambda img: "cell F18 value 45000"))
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")  # stub OCR must not enter the shared OCR cache
    tl = _timeline(tmp_path / "screen", [7.0])
    answer = _out(steps=[_step_rec("step-02", ["ev-001"], "F18 holds 45000.", [("ev-001", "F18 value 45000")])],
                  events=[EventAssignment(event_id="ev-001", step_id="step-02")])
    doc = pipeline._reconcile_stage(_FakeClient(structured_response=answer), tmp_path, _doc(), tl, RAW, TRANSCRIPT)
    assert doc.steps[1].observed_changes[0].text == "F18 holds 45000."
    assert (tmp_path / "change_records.json").exists()
