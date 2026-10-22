"""Deterministic build_spec.json generation -- the second of ProcessGhost's two renderings from
one source (see report/sop_render.py for the human-facing markdown). Consumed by a future build
agent, not a human directly: the same steps + flag registry as sop.md, plus a per-flag triage
verdict and a severity-ordered escalation queue of everything that still needs a human answer.

Kept as its own file rather than folded into sop_render.py: same "deterministic renderer of one
validated SOPDocument" role, but a different output shape for a different consumer.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from core.io.storage import save_json
from core.ladder import sme_questions
from core.models.sop import SCHEMA_VERSION, Flag, SOPDocument

PROCEED_WITH_ASSUMPTION = "proceed_with_assumption"
PROCEED_WITH_FLAG = "proceed_with_flag"
BLOCK_AND_ESCALATE = "block_and_escalate"

_SEVERITY_ORDER = {"blocking": 0, "caution": 1, "note": 2}


def triage_flag(flag: Flag) -> str:
    """Deterministic triage over (status, severity) alone -- auditable, replaceable later, per the
    v2 plan.

    status == "resolved" always proceeds with the Checker's inferred answer, regardless of the
    flag's original severity -- the question got answered. status == "contradicted" always
    escalates; _apply_resolution (core/pipeline.py) already forces severity="blocking" on a
    contradiction, so the explicit check here is for readability, not because the severity rule
    below wouldn't already catch it.

    Everything else -- "open" (never checked), "unresolvable" (checked, still no answer),
    "unchecked" (over the per-run Checker cap), "check_failed" (the Checker call itself failed) --
    is a question of unknown answer. It escalates only if it's severe enough to actually block
    someone; a caution/note-level unknown ships as a visible flag in the doc and nothing more.
    """
    if flag.status == "resolved":
        return PROCEED_WITH_ASSUMPTION
    if flag.status == "contradicted":
        return BLOCK_AND_ESCALATE
    if flag.severity == "blocking":
        return BLOCK_AND_ESCALATE
    return PROCEED_WITH_FLAG


def finding_kind(flag: Flag) -> str:
    """How much the evidence supports saying, as a deterministic function of the flag -- no model call.
    A kind the creating stage recorded wins (the Reconciler knows whether a flag is a claim not shown or
    an unspecified rule). Otherwise it follows the outcome. A flag the pipeline could not check says
    nothing about the process, and a claim the screen does not show is `unsupported_assertion`, never
    'disproved': absence of a demonstration is not disproof."""
    if flag.status in ("unchecked", "check_failed"):
        return "no_finding"
    if flag.status == "contradicted":
        return "potential_conflict"
    if flag.finding_kind is not None and flag.status != "resolved":
        return flag.finding_kind
    if flag.status == "resolved":
        return "observed_unexplained" if flag.family == "structural" else "explained_action"
    if flag.family == "structural" and flag.origin == "screen":
        return "observed_unexplained"
    return "unresolved_requirement"


def build_spec(doc: SOPDocument) -> dict:
    """Pure function of `doc` -- same doc always produces the same build_spec, mirroring
    report/sop_render.py: render_sop_md's determinism guarantee."""
    flags_out: List[dict] = []
    escalation_queue: List[dict] = []
    for flag in doc.flags:
        triage = triage_flag(flag)
        entry = {
            "flag_id": flag.flag_id,
            "family": flag.family,
            "question": flag.question,
            "severity": flag.severity,
            "status": flag.status,
            "step_id": flag.step_id,
            "between_step_ids": flag.between_step_ids,
            "window_segment_ids": flag.window_segment_ids,
            "window_time_range": flag.window_time_range,
            "origin": flag.origin,
            "event_ids": flag.event_ids,
            "checker_tier": flag.checker_tier,
            "finding_kind": finding_kind(flag),
            "slot": flag.slot,
            "rungs": [r.model_dump(mode="json") for r in flag.rungs],
            "triage": triage,
        }
        flags_out.append(entry)
        if triage == BLOCK_AND_ESCALATE:
            escalation_queue.append(entry)

    escalation_queue.sort(key=lambda e: _SEVERITY_ORDER.get(e["severity"], 3))

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": doc.run_id,
        "steps": [s.model_dump() for s in sorted(doc.steps, key=lambda s: s.order)],
        "flags": flags_out,
        "escalation_queue": escalation_queue,
        "sme_questions": sme_questions(doc),
        "provenance_complete": doc.provenance_complete,
        "review_failed": doc.review_failed,
        "timing_degraded": doc.timing_degraded,
    }


def write_build_spec(doc: SOPDocument, run_dir: Path) -> Path:
    return save_json(build_spec(doc), run_dir / "build_spec.json")
