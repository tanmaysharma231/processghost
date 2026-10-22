"""The process flow as a graph, with the gaps drawn in (docs/26 flow plan). A deterministic rendering of one
validated SOPDocument, like report/sop_render.py: the same document always gives the same flow.

What the flow promises, and what it does not. Every step that was said or shown is a node, marked by which. Every
question that is still open is a dashed gap node placed where the missing piece belongs: between two steps (a step
nobody explained), or hanging off a step (a data step whose source, key, target or effect nobody stated). A
question that was answered is not a node; its answer is on the step. The flow cannot prove nothing is missing
(an action off screen, a rule nobody mentioned and the data does not hold), only that every gap it knows about is
visible. A flow with no gap nodes therefore says "no known gaps", never "complete".

Outputs: flow.json (nodes, edges, annotations, summary) and flow.md (a Mermaid diagram).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from core.io.storage import save_json
from core.ladder import sme_text
from core.models.sop import Flag, SOPDocument, SOPStep
from core.screen.anchors import describe
from core.slots import open_slots

FLOW_SCHEMA_VERSION = "1.0"
_STATE = {"contradicted": "conflict", "unresolvable": "unresolved", "open": "unresolved", "unchecked": "not_examined", "check_failed": "not_examined"}
_TITLE_CHARS = 60
_GAP_CHARS = 80


def _source(step: SOPStep) -> str:
    kinds = {a.kind for a in step.attributions}
    said, shown = "said" in kinds, "shown" in kinds or bool(step.observed_changes)
    if said and shown:
        return "said+shown"
    if said:
        return "said"
    if shown:
        return "shown"
    return "inferred"


_CELL_VALUE = re.compile(r"^(?P<sheet>.+)!(?P<col>[A-Z]{1,3})(?P<row>\d+)(?: \((?P<header>.*?)\))?(?: value .*)?$")


def template_phrase(value: str) -> str:
    """A cell address written for reuse: the sheet and column are the process, the row is this run's item. The real address stays in the text."""
    m = _CELL_VALUE.match(value)
    if m is None:
        return value
    header = " (%s)" % m.group("header") if m.group("header") else ""
    return "%s, column %s%s, the row being processed (%s%s in this recording)" % (m.group("sheet"), m.group("col"), header, m.group("col"), m.group("row"))


def _step_node(step: SOPStep, notes: List) -> dict:
    node = {
        "id": step.step_id, "type": "result" if step.kind == "result" else "step", "order": step.order, "title": step.title, "source": _source(step),
        "kind": step.kind, "slots": {name: {"value": fill.value, "by": fill.by, "template": template_phrase(fill.value)} for name, fill in step.slots.items()},
        "open_slots": open_slots(step),
    }
    if step.kind == "result":
        events = sorted({e for oc in step.observed_changes for e in oc.event_ids})
        node["evidence"] = "shown" if events else "claimed_not_shown"
        node["event_ids"] = events
        node["mechanism"] = [n.text for n in notes if n.kind == "result_mechanism" and n.step_id == step.step_id]
    return node


def _gap_node(flag: Flag) -> dict:
    text, style = sme_text(flag)
    return {
        "id": "gap-" + flag.flag_id, "type": "gap", "flag_id": flag.flag_id, "state": _STATE.get(flag.status, "unresolved"),
        "severity": flag.severity, "question": text, "style": style, "slot": flag.slot, "step_id": flag.step_id,
        "between": list(flag.between_step_ids) if flag.between_step_ids else None,
        "tried": [{"rung": r.rung, "outcome": r.outcome} for r in flag.rungs],
    }


def build_flow(doc: SOPDocument) -> dict:
    """Pure function of `doc`."""
    all_steps = sorted(doc.steps, key=lambda s: s.order)
    steps = [s for s in all_steps if s.kind != "result"]  # the chain is the actions; a result hangs off the action that causes it
    by_id = {s.step_id: s for s in all_steps}
    nodes: List[dict] = [_step_node(s, doc.flow_notes) for s in all_steps]
    gaps = [_gap_node(f) for f in doc.flags if f.status != "resolved"]

    between: Dict[tuple, List[dict]] = {}
    attached: Dict[str, List[dict]] = {}
    unplaced: List[dict] = []
    for g in gaps:
        if g["between"] and tuple(g["between"]) in {(a.step_id, b.step_id) for a, b in zip(steps, steps[1:])}:
            between.setdefault(tuple(g["between"]), []).append(g)
        elif g["step_id"] in by_id:
            attached.setdefault(g["step_id"], []).append(g)
        else:
            unplaced.append(g)

    edges: List[dict] = []
    for a, b in zip(steps, steps[1:]):
        chain = between.get((a.step_id, b.step_id), [])
        if not chain:
            edges.append({"from": a.step_id, "to": b.step_id, "style": "solid", "label": ""})
            continue
        path = [a.step_id] + [g["id"] for g in chain] + [b.step_id]
        edges += [{"from": x, "to": y, "style": "dashed", "label": ""} for x, y in zip(path, path[1:])]
    for step_id, items in attached.items():
        edges += [{"from": step_id, "to": g["id"], "style": "dashed", "label": g["slot"] or ""} for g in items]
    for s in all_steps:
        if s.kind == "result":
            before = [a for a in steps if a.order < s.order]
            cause = before[-1] if before else (steps[0] if steps else None)
            if cause is not None:
                edges.append({"from": cause.step_id, "to": s.step_id, "style": "result", "label": "result"})

    placed = [g for items in list(between.values()) + list(attached.values()) for g in items] + unplaced
    nodes += sorted(placed, key=lambda g: g["flag_id"])
    annotations = [
        {"event_id": a.event_id, "text": describe(a), "confirmed": a.confirmed}
        for a in doc.anchors if a.role is not None
    ]
    sources = [n["source"] for n in nodes if n["type"] == "step"]
    return {
        "schema_version": FLOW_SCHEMA_VERSION, "run_id": doc.run_id, "nodes": nodes, "edges": edges, "annotations": annotations,
        "unplaced": [g["id"] for g in unplaced],
        "parameters": [p.model_dump(mode="json") for p in doc.parameters],
        "notes": [n.model_dump(mode="json", exclude={"citations"}) for n in doc.flow_notes if n.kind != "result_mechanism"],
        "summary": {
            "results": len(all_steps) - len(steps), "parameters": len(doc.parameters), "notes": sum(1 for n in doc.flow_notes if n.kind != "result_mechanism"),
            "steps": len(steps), "said": sources.count("said"), "shown": sources.count("shown"), "said_and_shown": sources.count("said+shown"),
            "inferred": sources.count("inferred"), "gaps": len(placed), "conflicts": sum(1 for g in placed if g["state"] == "conflict"),
            "not_examined": sum(1 for g in placed if g["state"] == "not_examined"),
        },
    }


def _mid(identifier: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", identifier)


def _label(text: str, limit: int) -> str:
    clean = " ".join(re.sub(r"[\"\[\]{}()<>|`]", " ", text).split())
    return clean if len(clean) <= limit else clean[: limit - 3].rstrip() + "..."


def render_mermaid(flow: dict) -> str:
    lines = ["flowchart TD"]
    for n in flow["nodes"]:
        if n["type"] == "step":
            tag = {"said": "", "shown": " (done on screen, not said)", "said+shown": " (said and shown)", "inferred": " (inferred from screen)"}[n["source"]]
            lines.append('    %s["%d. %s%s"]' % (_mid(n["id"]), n["order"], _label(n["title"], _TITLE_CHARS), tag))
        elif n["type"] == "result":
            lines.append('    %s(["Result: %s (%s)"])' % (_mid(n["id"]), _label(n["title"], _TITLE_CHARS), "shown" if n["evidence"] == "shown" else "claimed, not shown"))
        else:
            mark = {"conflict": "CONFLICT", "unresolved": "MISSING?", "not_examined": "NOT EXAMINED"}[n["state"]]
            lines.append('    %s{{"%s %s: %s"}}' % (_mid(n["id"]), mark, n["flag_id"], _label(n["question"], _GAP_CHARS)))
    for e in flow["edges"]:
        arrow = "-->" if e["style"] == "solid" else "-.->"  # dashed gaps and results both use the dotted arrow
        label = "|%s|" % _label(e["label"], 20) if e["label"] else ""
        lines.append("    %s %s%s %s" % (_mid(e["from"]), arrow, label, _mid(e["to"])))
    gap_ids = [_mid(n["id"]) for n in flow["nodes"] if n["type"] == "gap"]
    shown_ids = [_mid(n["id"]) for n in flow["nodes"] if n["type"] == "step" and n["source"] in ("shown", "said+shown")]
    lines.append("    classDef gap stroke-dasharray: 5 5,stroke-width:2px")
    lines.append("    classDef shown stroke-width:3px")
    if gap_ids:
        lines.append("    class %s gap" % ",".join(gap_ids))
    if shown_ids:
        lines.append("    class %s shown" % ",".join(shown_ids))
    return "\n".join(lines)


def render_flow_md(flow: dict) -> str:
    s = flow["summary"]
    head = "%d step(s): %d said, %d shown, %d said and shown%s." % (
        s["steps"], s["said"], s["shown"], s["said_and_shown"], ", %d inferred from the screen" % s["inferred"] if s["inferred"] else "")
    if s["gaps"]:
        tail = "%d gap(s) marked%s." % (s["gaps"], " (%d conflict(s), %d not examined)" % (s["conflicts"], s["not_examined"]) if s["conflicts"] or s["not_examined"] else "")
    else:
        tail = "No known gaps. That is not proof nothing is missing: an action off screen, or a rule nobody mentioned that the data does not hold, would not appear here."
    lines = ["# Process flow -- " + flow["run_id"], "", head + " " + tail, "", "```mermaid", render_mermaid(flow), "```", ""]
    if flow["parameters"]:
        lines += ["## Values that probably change each run", ""]
        for prm in flow["parameters"]:
            lines.append("- **%s**: '%s' in this recording. %s" % (prm["name"], prm["example"], " ".join(prm["evidence"])))
        lines.append("")
    if flow["notes"]:
        lines += ["## Potential future issues if this is run for another period", ""] + ["- %s" % n["text"] for n in flow["notes"]] + [""]
    if flow["annotations"]:
        lines += ["## Selections tied to speech or values", ""] + ["- %s" % a["text"] + ("" if a["confirmed"] else " (a possibility, not confirmed)") for a in flow["annotations"]] + [""]
    return "\n".join(lines)


def write_flow(doc: SOPDocument, run_dir: Path) -> Path:
    flow = build_flow(doc)
    (run_dir / "flow.md").write_text(render_flow_md(flow), encoding="utf-8")
    return save_json(flow, run_dir / "flow.json")
