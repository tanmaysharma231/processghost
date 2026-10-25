"""The facts table around the touched cells, and the one-call question stage behind its guard. A synthetic workbook with
neutral names and a fake client: no model, no real data. The workbook has a column holding several outcomes, another that
differs between rows with the same outcome, and a formula column computed from two others."""
from __future__ import annotations

import json

import openpyxl
import pytest
from openpyxl.worksheet.datavalidation import DataValidation

from core import config
from core.data.context_facts import build_context_facts
from core.data.guard import changed_cells
from core.data.profile import profile_workbook
from core.data.tools import WorkbookTools
from core.data.wonder import WonderOutput, WonderQuestion, add_wonder_questions
from core.models.sop import SlotFill
from core.screen.cell_trail import TrailCell
from core.slots import fill_slot
from tests.fakes import _FakeClient
from tests.test_slots import _move, _review

ROWS = [  # item, cost, price, result, who
    ("A-1", 100, 110, "Done", "Auto"),
    ("A-2", 100, 120, "Open", None),
    ("A-3", 100, 130, "Done", "Ann"),
    ("A-4", 100, 140, "Done", "Bob"),
    ("A-5", 100, 150, "Held", None),
    ("A-6", 100, 160, "Open", None),
    ("A-7", 100, 105, "Done", "Auto"),
    ("A-8", 100, 106, "Done", "Auto"),
]


def _book(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Log"
    ws.append(["Item", "Cost", "Price", "Diff", "Result", "Who"])
    for i, (item, cost, price, result, who) in enumerate(ROWS, start=2):
        ws.append([item, cost, price, "=C%d-B%d" % (i, i), result, who])
    dv = DataValidation(type="list", formula1='"Open,Done,Held,Void"')
    dv.add("E2:E9")
    by_range = DataValidation(type="list", formula1="$H$1:$H$3")
    by_range.add("F2:F9")
    ws.add_data_validation(dv)
    ws.add_data_validation(by_range)
    wb.save(path)
    return path


@pytest.fixture
def world(tmp_path):
    tools = WorkbookTools(_book(tmp_path / "w.xlsx"), tmp_path / "work")
    return tools, profile_workbook(tools.copy_path)


def _doc():
    doc = _review([_move()])
    return fill_slot(doc, "step-02", "target", SlotFill(value="Log!E3 (Result)", by="shown", event_ids=["ev-b"]))


TRAIL = [TrailCell(event_id="ev-b", t_s=2.0, sheet="Log", cell="E3", content="Done")]


def _table(world):
    tools, profile = world
    return build_context_facts(changed_cells(_doc(), TRAIL), TRAIL, profile, tools)


def _text(table):
    return "\n".join(f.text for f in table.facts)


# --- the profile reads literal dropdown lists only ---------------------------------------------------------


def test_a_literal_dropdown_list_is_read_and_a_range_list_is_not(world):
    _tools, profile = world
    cols = {c.letter: c for c in profile.sheets[0].columns}
    assert cols["E"].allowed == ["Open", "Done", "Held", "Void"] and cols["F"].allowed == []


# --- the facts table -----------------------------------------------------------------------------------------


def test_the_changed_cell_is_described_with_what_the_recording_shows_and_what_the_file_holds(world):
    text = _text(_table(world))
    assert "changes Log!E3 (column E 'Result') and it shows 'Done'. The file holds 'Open' in that cell." in text


def test_the_column_fact_has_counts_and_the_dropdown_options_never_used(world):
    text = _text(_table(world))
    assert "Done (5)" in text and "Open (2)" in text and "Held (1)" in text
    assert "Its dropdown allows: Open, Done, Held, Void." in text and "Never used in the rows: Void." in text


def test_other_columns_of_the_same_rows_are_grouped_by_the_changed_column_including_a_computed_formula_column(world):
    text = _text(_table(world))
    assert "Done: Auto (3), Ann (1), Bob (1)" in text  # who handled the rows with the same outcome
    assert "Open: (blank) (2)" in text
    assert "Done: 5 to 40 (5 rows)" in text  # Diff is =C-B per row, computed from the file's values (no stored results exist)


def test_a_cell_nothing_reads_is_reported_as_read_by_no_formula(world):
    assert "0 formula(s) in the workbook read Log!E3." in _text(_table(world))


def test_no_row_identifier_or_free_row_is_listed(world):
    text = _text(_table(world))
    assert not any(item in text for item, *_rest in ROWS)  # a unique identifier column is described, never listed


def test_facts_are_numbered_and_capped_with_the_cut_counted(world, monkeypatch):
    monkeypatch.setattr(config, "WONDER_MAX_FACTS", 2)
    table = _table(world)
    assert [f.fact_id for f in table.facts] == ["F1", "F2"] and table.dropped > 0


def test_nothing_touched_gives_no_facts(world):
    tools, profile = world
    assert build_context_facts([], [], profile, tools).facts == []


# --- the one call and its guard ------------------------------------------------------------------------------


def _ids(world):
    table = _table(world)
    return {
        "who": next(f.fact_id for f in table.facts if "grouped by column E" in f.text and "column F 'Who'" in f.text),
        "column": next(f.fact_id for f in table.facts if f.text.startswith("Column E 'Result'")),
    }


SEEN = "Among the rows that are 'Done', Who holds two people's names and 'Auto' (3)."


def _q(question, fact_ids, observed=SEEN, **kw):
    return WonderQuestion(observed=observed, question=question, fact_ids=fact_ids, **kw)


def _good(ids):
    return _q("The recording sets 'Done', and 'Auto' appears under Who for some rows with that outcome; what decides which one applies?",
              [ids["who"]], step_id="step-02")


def _run(world, questions, doc=None):
    tools, profile = world
    client = _FakeClient(structured_response=WonderOutput(questions=questions))
    client.metrics.trace_path = profile_trace(world)
    return client, add_wonder_questions(client, doc or _doc(), TRAIL, profile, tools)


def profile_trace(world):
    """A trace file next to the workbook copy, so the guard's payload line can be read back."""
    return world[0].copy_path.parent / "trace.jsonl"


def _guard_line(world):
    lines = [json.loads(x) for x in profile_trace(world).read_text(encoding="utf-8").splitlines()]
    return next(x["payload"] for x in lines if x.get("stage") == "wonder_guard")


def test_a_grounded_question_becomes_a_flag_for_the_person_with_its_cells(world):
    client, doc = _run(world, [_good(_ids(world))])
    new = [f for f in doc.flags if f.origin == "data"]
    assert len(new) == 1 and new[0].status == "unresolvable" and new[0].step_id == "step-02" and new[0].question.endswith("?")
    assert new[0].rungs[0].citations[0].ref == "E3" and "column F 'Who'" in new[0].rungs[0].reason
    assert doc.provenance_complete and len(client.structured_calls) == 1


def test_ungrounded_questions_are_dropped_and_counted(world):
    ids = _ids(world)
    who = ids["who"]
    bad = [
        _q("Is 1000 the limit for 'Done'?", [who]),  # a number that is not in the facts
        _q("Is 'Zeta' the usual value?", [who]),  # a value that is not in the facts
        _q("What decides 'Done'?", ["F99"]),  # a fact that does not exist
        _q("What decides 'Done'?", []),  # no fact at all
        _q("'Auto' is applied to the rows because of their size.", [who]),  # not a question
        _q("Is 'Auto' used because the cost is low?", [who]),  # states a reason
        _q("What decides 'Done'?", [who], observed=""),  # no observation written first
        _q("What decides 'Done'?", [who], observed="'Done' appears on 1000 rows."),  # the observation has a number that is not in the facts
        _q("What decides 'Done'?", [who], observed="Most rows are 'Mystery'."),  # the observation has a value that is not in the facts
    ]
    _client, doc = _run(world, bad + [_good(ids)])
    assert len([f for f in doc.flags if f.origin == "data"]) == 1
    guard = _guard_line(world)
    assert guard["asked"] == 10 and guard["kept"] == 1 and len(guard["dropped"]) == 9
    assert {d["reason"].split(" ")[0] for d in guard["dropped"]} >= {"the", "cites", "not", "states", "no"}


def test_the_observation_is_kept_on_the_flag_so_the_reader_sees_what_the_question_rests_on(world):
    _client, doc = _run(world, [_good(_ids(world))])
    flag = next(f for f in doc.flags if f.origin == "data")
    assert SEEN in flag.rungs[0].reason


def test_a_possessive_apostrophe_is_not_read_as_a_quote(world):
    q = _q("What decides when a row's result is set to 'Done' and not 'Open', and who sets the row's value?", [_ids(world)["column"]],
           observed="The column holds 'Done' and 'Open', and the row's value is one of them.")
    _client, doc = _run(world, [q])
    assert len([f for f in doc.flags if f.origin == "data"]) == 1


def test_asking_again_adds_nothing(world):
    ids = _ids(world)
    _client, once = _run(world, [_good(ids)])
    _client, twice = _run(world, [_good(ids)], doc=once)
    assert len(twice.flags) == len(once.flags)


def test_a_failing_call_leaves_the_document_unchanged_and_is_recorded(world):
    tools, profile = world
    client = _FakeClient(structured_response=RuntimeError("model down"))
    doc = _doc()
    assert add_wonder_questions(client, doc, TRAIL, profile, tools) == doc
    assert any(f["stage"] == "wonder_failed" for f in client.metrics.failures)


def test_nothing_changed_makes_no_call(world):
    tools, profile = world
    client = _FakeClient(structured_response=WonderOutput())
    doc = _review([_move()])
    assert add_wonder_questions(client, doc, [], profile, tools) == doc and client.structured_calls == []
