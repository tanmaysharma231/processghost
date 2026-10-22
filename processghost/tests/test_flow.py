"""The process flow graph with its gaps: said/shown marks, gap nodes placed where the missing piece belongs,
answered questions not drawn, deterministic output. Synthetic documents: no model, no video."""
from __future__ import annotations

import json
import re

from core.data.apply import apply_outcome
from core.models.data import DataCitation
from core.models.reconcile import ReconcilerOutput
from core.models.screen import SelectionAnchor
from core.models.sop import Flag, SlotFill
from core.screen.reconcile import merge_reconciler_output
from core.slots import fill_slot
from report.flow import build_flow, render_flow_md, render_mermaid, write_flow
from tests.test_data_checker import _outcome
from tests.test_reconcile import RECORDS, TRANSCRIPT, _doc
from tests.test_shown_steps import OWNED, _assign, _unowned
from tests.test_slots import _move, _review


def _nodes(flow, kind):
    return [n for n in flow["nodes"] if n["type"] == kind]


def test_a_plain_document_is_a_solid_chain_of_said_steps():
    flow = build_flow(_doc())
    assert [n["source"] for n in _nodes(flow, "step")] == ["said", "said", "said"]
    assert [(e["from"], e["to"], e["style"]) for e in flow["edges"]] == [("step-01", "step-02", "solid"), ("step-02", "step-03", "solid")]
    assert flow["summary"]["gaps"] == 0 and "No known gaps" in render_flow_md(flow) and "not proof" in render_flow_md(flow)


def test_a_step_missing_between_two_steps_is_a_dashed_gap_node_in_the_chain():
    doc = _doc().model_copy(update={"flags": [Flag(flag_id="FLAG-01", family="structural", question="What happens between them?", severity="blocking",
                                                   window_segment_ids=[1], between_step_ids=("step-01", "step-02"))]})
    flow = build_flow(doc)
    path = [(e["from"], e["to"], e["style"]) for e in flow["edges"]]
    assert path[:2] == [("step-01", "gap-FLAG-01", "dashed"), ("gap-FLAG-01", "step-02", "dashed")] and path[2][2] == "solid"
    assert _nodes(flow, "gap")[0]["state"] == "unresolved"
    text = render_mermaid(flow)
    assert "step_01 -.-> gap_FLAG_01" in text and "gap_FLAG_01 -.-> step_02" in text and "class gap_FLAG_01 gap" in text


def test_open_slots_hang_off_their_step_and_answered_ones_are_not_drawn():
    doc = _review([_move()])
    doc = fill_slot(doc, "step-02", "target", SlotFill(value="F18", by="shown", event_ids=["ev-1"]))
    flow = build_flow(doc)
    slot_gaps = {n["slot"] for n in _nodes(flow, "gap") if n["slot"]}
    assert slot_gaps == {"source", "key", "effect"}
    step = next(n for n in _nodes(flow, "step") if n["id"] == "step-02")
    assert step["slots"] == {"target": {"value": "F18", "by": "shown", "template": "F18"}} and step["open_slots"] == ["source", "key", "effect"]
    assert {(e["from"], e["label"]) for e in flow["edges"] if e["to"].startswith("gap-") and e["from"] == "step-02"} >= {("step-02", "key")}


def test_a_step_done_on_screen_is_marked_shown_and_a_said_step_with_observed_changes_is_both():
    out = ReconcilerOutput(events=OWNED + [_assign("ev-004", "A cell was set to Paid.", ["Status Paid"])])
    doc = merge_reconciler_output(_doc(), out, list(RECORDS) + [_unowned(4, 9.0)], TRANSCRIPT)
    flow = build_flow(doc)
    by = {n["id"]: n["source"] for n in _nodes(flow, "step")}
    assert by["step-s01"] == "shown" and by["step-01"] == "said"
    text = render_mermaid(flow)
    assert "(done on screen, not said)" in text and "class step_s01 shown" in text


def test_a_conflict_and_a_question_nobody_examined_are_distinguished():
    doc = _review([_move()])
    key = next(f for f in doc.flags if f.slot == "key")
    contradicted = apply_outcome(doc, key.flag_id, _outcome("contradicted", "the formula reads another column", [DataCitation(workbook="b", sheet="s", ref="A1")]))
    flow = build_flow(contradicted)
    states = {n["flag_id"]: n["state"] for n in _nodes(flow, "gap")}
    assert states[key.flag_id] == "conflict" and flow["summary"]["conflicts"] == 1
    unchecked = doc.model_copy(update={"flags": [f.model_copy(update={"status": "unchecked"}) for f in doc.flags]})
    assert build_flow(unchecked)["summary"]["not_examined"] == len(unchecked.flags)
    assert "NOT EXAMINED" in render_mermaid(build_flow(unchecked))


def test_a_gap_with_nowhere_to_attach_is_listed_as_unplaced_not_lost():
    doc = _doc().model_copy(update={"flags": [Flag(flag_id="FLAG-01", family="structural", question="What happened here?", severity="caution",
                                                   window_time_range=(1.0, 2.0))]})
    flow = build_flow(doc)
    assert flow["unplaced"] == ["gap-FLAG-01"] and flow["summary"]["gaps"] == 1


def test_a_confirmation_style_question_carries_the_candidates():
    doc = _review([_move()])
    key = next(f for f in doc.flags if f.slot == "key")
    done = apply_outcome(doc, key.flag_id, _outcome("candidates", candidates=["Code", "Name"]))
    gap = next(n for n in _nodes(build_flow(done), "gap") if n["flag_id"] == key.flag_id)
    assert gap["style"] == "confirm" and gap["question"].endswith("Code or Name?") and gap["tried"][-1] == {"rung": "document", "outcome": "candidates"}


def test_labels_cannot_break_the_diagram_and_anchors_are_worded_as_possibilities():
    doc = _doc().model_copy(update={
        "flags": [Flag(flag_id="FLAG-01", family="ambiguity", question='Is "x" [here] {ok} <b>?', severity="caution", window_segment_ids=[0], step_id="step-01")],
        "anchors": [SelectionAnchor(event_id="ev-1", t_s=1.0, focus="C2", role="pointer")],
    })
    flow = build_flow(doc)
    text = render_mermaid(flow)
    label = re.search(r'gap_FLAG_01\{\{"(.*)"\}\}', text).group(1)
    assert not any(ch in label for ch in '"[]{}()<>|`') and "Is x here ok b ?" in label
    assert "possibly C2" in render_flow_md(flow) and "not confirmed" in render_flow_md(flow)


def test_the_flow_is_deterministic_and_written_next_to_the_report(tmp_path):
    doc = _review([_move()])
    assert json.dumps(build_flow(doc)) == json.dumps(build_flow(doc))
    write_flow(doc, tmp_path)
    assert json.loads((tmp_path / "flow.json").read_text(encoding="utf-8"))["summary"]["steps"] == 3
    assert "```mermaid" in (tmp_path / "flow.md").read_text(encoding="utf-8")
