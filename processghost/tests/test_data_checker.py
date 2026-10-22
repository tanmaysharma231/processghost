"""The document rung: the lookup loop, citation checking, merging outcomes into the document, routing, and the
questions that remain for the person. A fake client and a synthetic workbook: no model, no real data."""
from __future__ import annotations

import pytest

from core import config, pipeline
from core.data.apply import apply_outcome
from core.data.checker import CallArgs, CellRef, DataCheckerStep, DataOutcome, FoundQuestion, NewQuestion, check_flag_with_data
from core.data.profile import profile_workbook
from core.data.tools import Filter, WorkbookTools
from core.ladder import record_video_rungs, route_to_document, sme_questions, sme_text
from core.models.data import DataCitation, RungAttempt
from core.models.sop import Flag, Resolution, SOPDocument
from tests.fakes import _FakeClient
from tests.test_data_profile_tools import _book
from tests.test_slots import _move, _review


@pytest.fixture
def world(tmp_path):
    path = _book(tmp_path / "book.xlsx")
    tools = WorkbookTools(path, tmp_path / "work")
    return tools, profile_workbook(tools.copy_path).model_copy(update={"workbook": tools.name}), tmp_path


def _flag(doc, slot):
    return next(f for f in doc.flags if f.slot == slot)


def _query(reason="look", **kw):
    return DataCheckerStep(action="tool", reason=reason, call=CallArgs(tool="query_rows", sheet="Items", **kw))


def _verdict(verdict, answer="", cites=(), candidates=(), new=()):
    return DataCheckerStep(action="verdict", reason="r", verdict=verdict, answer=answer,
                           citations=[CellRef(sheet=s, ref=r) for s, r in cites], candidates=list(candidates), new_questions=list(new))


def _run(world, steps, slot="key", **kw):
    tools, profile, _ = world
    doc = _review([_move()])
    client = _FakeClient(structured_response=list(steps))
    return doc, client, check_flag_with_data(client, _flag(doc, slot), doc, profile, tools, **kw)


# --- the loop --------------------------------------------------------------------------------


def test_a_lookup_then_a_verdict_citing_a_returned_cell_is_answered_with_the_cells_own_text(world):
    steps = [
        _query(filters=[Filter(column="Code", op="eq", value="A-1")], columns=["Qty"]),
        _verdict("answered", "Quantity is 10", [("Items", "C4")]),
    ]
    _doc, client, out = _run(world, steps)
    assert out.attempt.outcome == "answered" and out.calls == 1 and out.wasted == 0
    cite = out.attempt.citations[0]
    assert (cite.sheet, cite.ref, cite.value, cite.workbook) == ("Items", "C4", "10", "book.xlsx")
    assert len(client.structured_calls) == 2 and "lookups_so_far" in client.structured_calls[1][1]


def test_the_formula_text_is_cited_from_the_lookup_not_from_the_model(world):
    steps = [DataCheckerStep(action="tool", reason="find the rule", call=CallArgs(tool="read_rows", sheet="Items", start=10, limit=1)),
             _verdict("answered", "The total counts rows flagged x", [("Items", "c10")])]
    _doc, _client, out = _run(world, steps)
    assert out.attempt.citations[0].formula.startswith("=SUMIF") and out.attempt.citations[0].ref == "C10"


def test_a_citation_to_a_cell_no_lookup_returned_is_dropped_and_the_verdict_downgraded(world):
    steps = [_query(filters=[Filter(column="Code", op="eq", value="A-1")]), _verdict("answered", "It is Z", [("Items", "Z99")])]
    _doc, _client, out = _run(world, steps)
    assert out.attempt.outcome == "no_answer" and out.attempt.citations == [] and "nothing checkable" in out.attempt.reason


def test_a_verdict_without_any_lookup_cannot_be_answered(world):
    _doc, client, out = _run(world, [_verdict("answered", "Sure", [("Items", "C4")])])
    assert out.attempt.outcome == "no_answer" and out.calls == 0


def test_only_the_verified_citations_survive(world):
    steps = [_query(filters=[Filter(column="Code", op="eq", value="A-1")]), _verdict("answered", "x", [("Items", "A4"), ("Items", "Q1")])]
    _doc, _client, out = _run(world, steps)
    assert [c.ref for c in out.attempt.citations] == ["A4"]


def test_lookups_are_capped_and_the_last_round_can_only_give_a_verdict(world):
    steps = [_query(reason="a", limit=1), _query(reason="b", limit=2), _query(reason="c", limit=3)]  # never gives a verdict
    _doc, client, out = _run(world, steps, max_calls=2)
    assert out.attempt.outcome == "no_answer" and out.calls == 2 and len(client.structured_calls) == 3
    assert "No lookups are left" in client.structured_calls[2][1]


def test_an_identical_repeated_lookup_is_refused_and_counted_as_wasted(world):
    same = _query(filters=[Filter(column="Code", op="eq", value="A-1")])
    _doc, client, out = _run(world, [same, same, _verdict("no_answer")])
    assert out.calls == 2 and out.wasted == 1 and "already made" in client.structured_calls[2][1]


def test_a_bad_column_is_an_error_the_model_can_read_and_a_wasted_call(world):
    _doc, client, out = _run(world, [_query(filters=[Filter(column="Nope", op="eq", value="1")]), _verdict("no_answer")])
    assert out.wasted == 1 and "unknown column" in client.structured_calls[1][1]


def test_candidates_need_at_least_one_candidate_and_keep_them(world):
    _d, _c, none = _run(world, [_verdict("candidates")])
    assert none.attempt.outcome == "no_answer"
    steps = [_query(filters=[Filter(column="Flag", op="eq", value="x")]), _verdict("candidates", "", [("Items", "A4")], ["Code", "Name"])]
    _d, _c, some = _run(world, steps)
    assert some.attempt.outcome == "candidates" and some.attempt.candidates == ["Code", "Name"]


def test_new_questions_need_verified_cells_and_are_never_answered_by_the_same_call(world):
    good = NewQuestion(question="Does column Flag hold what its header says?", citations=[CellRef(sheet="Items", ref="D4")])
    bad = NewQuestion(question="Invented?", citations=[CellRef(sheet="Items", ref="Q1")])
    steps = [_query(filters=[Filter(column="Code", op="eq", value="A-1")]), _verdict("no_answer", new=[good, bad])]
    _d, _c, out = _run(world, steps)
    assert [q.question for q in out.new_questions] == ["Does column Flag hold what its header says?"]


# --- merging an outcome ---------------------------------------------------------------------------


def _outcome(kind, answer="", cites=(), candidates=(), found=()):
    attempt = RungAttempt(rung="document", outcome=kind, answer=answer, citations=list(cites), candidates=list(candidates))
    return DataOutcome(attempt, list(found), 1, 0)


CITE = DataCitation(workbook="book.xlsx", sheet="Items", ref="C10", formula="=SUMIF(...)")


def test_an_answer_resolves_a_slot_question_and_fills_the_slot_with_the_cells():
    doc = _review([_move()])
    flag = _flag(doc, "key")
    done = apply_outcome(doc, flag.flag_id, _outcome("answered", "rows are matched by Code", [CITE]))
    fill = done.steps[1].slots["key"]
    assert fill.by == "document" and fill.citations == [CITE]
    assert _flag(done, "key").status == "resolved" and _flag(done, "key").rungs[-1].outcome == "answered" and done.provenance_complete


def test_an_answer_resolves_an_ordinary_question_with_a_resolution_that_cites_the_cells():
    doc = SOPDocument(run_id="r", steps=_review().steps, flags=[Flag(flag_id="FLAG-01", family="ambiguity", question="q?", severity="caution",
                                                                      window_segment_ids=[1], step_id="step-02", status="unresolvable")])
    done = apply_outcome(doc, "FLAG-01", _outcome("answered", "yes", [CITE]))
    res = done.resolutions[0]
    assert done.flags[0].status == "resolved" and res.citations == [CITE] and res.inferred_text == "yes" and done.provenance_complete


def test_a_contradiction_overrides_a_question_the_video_resolved_and_goes_to_the_person():
    doc = _review([_move()])
    flag = _flag(doc, "target")
    doc = doc.model_copy(update={
        "flags": [f.model_copy(update={"status": "resolved"}) if f.flag_id == flag.flag_id else f for f in doc.flags],
        "resolutions": [Resolution(flag_id=flag.flag_id, verdict="resolved", inferred_text="column F", event_ids=["ev-001"])],
    })
    done = apply_outcome(doc, flag.flag_id, _outcome("contradicted", "the formula reads column H", [CITE]))
    f = _flag(done, "target")
    res = next(r for r in done.resolutions if r.flag_id == f.flag_id)
    assert f.status == "contradicted" and f.severity == "blocking" and res.narration_version == "column F" and res.screen_version == "the formula reads column H"
    assert done.provenance_complete and len([r for r in done.resolutions if r.flag_id == f.flag_id]) == 1


def test_candidates_leave_a_question_unresolvable_and_the_person_gets_a_confirmation():
    doc = _review([_move()])
    flag = _flag(doc, "key")
    done = apply_outcome(doc, flag.flag_id, _outcome("candidates", cites=[CITE], candidates=["Code", "Name"]))
    f = _flag(done, "key")
    assert f.status == "unresolvable" and done.provenance_complete
    text, style = sme_text(f)
    assert style == "confirm" and text.endswith("Which is it: Code or Name?")
    assert [q["style"] for q in sme_questions(done) if q["flag_id"] == f.flag_id] == ["confirm"]


def test_no_answer_never_touches_a_resolved_question():
    doc = _review([_move()])
    flag = _flag(doc, "key")
    resolved = doc.model_copy(update={"flags": [f.model_copy(update={"status": "resolved"}) if f.flag_id == flag.flag_id else f for f in doc.flags],
                                      "resolutions": [Resolution(flag_id=flag.flag_id, verdict="resolved", inferred_text="x", event_ids=["ev-1"])]})
    done = apply_outcome(resolved, flag.flag_id, _outcome("no_answer"))
    assert _flag(done, "key").status == "resolved" and _flag(done, "key").rungs[-1].outcome == "no_answer"


def test_a_conflict_the_data_revealed_becomes_its_own_flag_for_the_person():
    doc = _review([_move()])
    flag = _flag(doc, "key")
    found = FoundQuestion("Do the stored values match the rule?", "blocking", [CITE])
    done = apply_outcome(doc, flag.flag_id, _outcome("no_answer", found=[found]))
    new = done.flags[-1]
    assert new.origin == "data" and new.status == "unresolvable" and new.finding_kind == "potential_conflict" and new.step_id == flag.step_id
    assert next(r for r in done.resolutions if r.flag_id == new.flag_id).citations == [CITE] and done.provenance_complete
    assert len(apply_outcome(done, flag.flag_id, _outcome("no_answer", found=[found])).flags) == len(done.flags)  # no duplicate question


# --- routing and the ladder --------------------------------------------------------------------------


def test_the_video_rung_is_recorded_for_every_flag_once():
    doc = record_video_rungs(_review([_move()]))
    assert all(f.rungs and f.rungs[0].rung == "video" and f.rungs[0].outcome == "skipped" for f in doc.flags)
    assert record_video_rungs(doc).flags[0].rungs == doc.flags[0].rungs


def test_rule_questions_go_first_and_overflow_is_reported_not_dropped(monkeypatch):
    monkeypatch.setattr(config, "DATA_MAX_ROUTED_FLAGS", 2)
    doc = _review([_move()])
    routed, overflow = route_to_document(doc, True)
    assert [f.slot for f in routed] == ["key", "effect"] and [f.slot for f in overflow] == ["target", "source"]  # blocking before caution
    assert route_to_document(doc, False) == ([], [])


def test_a_question_the_video_resolved_is_cross_checked_only_if_it_would_block():
    doc = _review([_move()])
    resolved = doc.model_copy(update={"flags": [f.model_copy(update={"status": "resolved"}) for f in doc.flags]})
    routed, _ = route_to_document(resolved, True)
    assert {f.slot for f in routed} == {"key", "target"}  # the blocking slots; the caution slots are not re-checked
    assert all(f.severity == "blocking" for f in routed)


def test_general_questions_are_not_sent_to_the_document_rung_unless_asked(monkeypatch):
    doc = _review([_move()])
    assert all(f.slot for f in route_to_document(doc, True)[0] + route_to_document(doc, True)[1])
    monkeypatch.setattr(config, "DATA_ROUTE_NON_SLOT", True)
    assert any(f.slot is None for f in route_to_document(doc, True)[0] + route_to_document(doc, True)[1])


def test_a_question_the_document_already_tried_is_not_sent_again():
    doc = _review([_move()])
    flag = _flag(doc, "key")
    tried = apply_outcome(doc, flag.flag_id, _outcome("no_answer"))
    assert flag.flag_id not in {f.flag_id for f in route_to_document(tried, True)[0]}


# --- the stage -------------------------------------------------------------------------------------------


def _stage(tmp_path, steps, doc=None):
    path = _book(tmp_path / "book.xlsx")
    client = _FakeClient(structured_response=list(steps))
    out = pipeline._document_stage(client, tmp_path / "run", doc or record_video_rungs(_review([_move()])), path)
    return client, out, path


def test_the_stage_answers_what_it_can_skips_the_overflow_and_leaves_the_workbook_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_MAX_ROUTED_FLAGS", 1)
    steps = [_query(filters=[Filter(column="Code", op="eq", value="A-1")]), _verdict("answered", "by Code", [("Items", "A4")])]
    client, doc, path = _stage(tmp_path, steps)
    assert _flag(doc, "key").status == "resolved" and doc.steps[1].slots["key"].by == "document"
    skipped = [f for f in doc.flags if f.rungs and f.rungs[-1].rung == "document" and f.rungs[-1].outcome == "skipped"]
    assert len(skipped) == 3 and "cap" in skipped[0].rungs[-1].reason
    assert (tmp_path / "run" / "workbook_profile.json").exists() and not client.metrics.failures


def test_a_failing_question_is_skipped_with_a_reason_and_the_others_still_run(tmp_path):
    steps = [RuntimeError("model down"), _verdict("no_answer")]
    client, doc, _ = _stage(tmp_path, steps)
    reasons = [f.rungs[-1].reason for f in doc.flags if f.rungs and f.rungs[-1].rung == "document"]
    assert any("failed" in r for r in reasons) and any(f["stage"] == "document_check_failed" for f in client.metrics.failures)


def test_the_spend_limit_skips_the_remaining_questions_with_a_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_PASS_BUDGET_USD", 0.0)
    client, doc, _ = _stage(tmp_path, [])
    attempts = [f.rungs[-1] for f in doc.flags if f.rungs[-1].rung == "document"]
    assert attempts and all(a.outcome == "skipped" and "spend limit" in a.reason for a in attempts)


def test_a_broken_workbook_fails_soft_and_returns_the_document_unchanged(tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a workbook")
    client = _FakeClient()
    doc = record_video_rungs(_review([_move()]))
    assert pipeline._document_stage(client, tmp_path / "run", doc, bad) is doc
    assert any(f["stage"] == "document_failed" for f in client.metrics.failures)


# --- behaviours the audit found untested ---------------------------------------------------------------


def test_a_lookup_action_with_no_call_is_no_answer_not_a_crash(world):
    _doc, _client, out = _run(world, [DataCheckerStep(action="tool", reason="forgot the call")])
    assert out.attempt.outcome == "no_answer" and out.calls == 0


def test_pipeline_limitations_are_not_questions_for_the_person():
    doc = _review([_move()])
    limited = doc.model_copy(update={"flags": [f.model_copy(update={"status": "unchecked"}) if f.slot == "key" else f for f in doc.flags]})
    asked = {q["flag_id"] for q in sme_questions(limited)}
    assert _flag(limited, "key").flag_id not in asked and _flag(limited, "target").flag_id in asked


def test_a_conflict_is_worded_as_a_conflict_not_an_open_question():
    doc = _review([_move()])
    flag = _flag(doc, "target")
    done = apply_outcome(doc, flag.flag_id, _outcome("contradicted", "another column", [CITE]))
    text, style = sme_text(_flag(done, "target"))
    assert style == "conflict" and text.startswith("The sources disagree") and [q["style"] for q in sme_questions(done) if q["flag_id"] == flag.flag_id] == ["conflict"]
