"""Deterministic markdown rendering for the v2 SOPDocument.

New file rather than an extension of report/render.py, which is typed against
core.models.tokens.RunArtifacts/Dissonance and is deleted in Phase 3 once the legacy
align/rules pipeline is retired.

The LLM (Writer, and later Reviewer/Checker) emits JSON only -- never markdown directly -- and
this module is the single deterministic renderer from that JSON into sop.md. This is a deliberate
choice, not an oversight: an LLM-authored markdown blob would (a) let the rendered doc drift from
the validated SOPDocument model, (b) prevent the Reviewer from referencing step ids that are
guaranteed to exist, and (c) break the "diff sop.md against the previous run" ritual the project
owner uses to sanity-check every session, which needs byte-deterministic output.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from core.io.storage import save_json
from core.models.sop import SOPDocument


def fmt_ts(t: float) -> str:
    """Format a timestamp in seconds as "M:SS" (or "MM:SS" / "H:MM:SS" for longer videos)."""
    total_seconds = int(round(max(0.0, t)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _render_attribution_tag(a) -> str:
    """`[said @t]` for Writer-sourced attributions, `[inferred from screen @t]` for a Checker-
    inserted structural step's attribution (Phase 3) -- the tag must follow `a.kind`, not assume
    every attribution came from narration, now that a step can exist with no `[said]` at all."""
    if a.kind == "inferred":
        return f"`[inferred from screen @{fmt_ts(a.t_s)}]`"
    return f"`[said @{fmt_ts(a.t_s)}]`"


_SEVERITY_ORDER = {"blocking": 0, "caution": 1, "note": 2}

# Statuses that mean "the pipeline never got a real answer from the video" -- as opposed to
# "resolved", the only status that means the document itself now contains the answer.
_NOT_CHECKED_STATUSES = {"open", "unchecked", "check_failed"}

_NOT_CHECKED_LABELS = {
    "open": "not yet checked (this is a pre-Checker draft)",
    "unchecked": "not checked -- over this run's flag cap",
    "check_failed": "not checked -- the check itself failed",
}

# Of _NOT_CHECKED_STATUSES, these two are pipeline infrastructure failing to even attempt an
# answer (a rate limit, an over-cap flag never selected) -- not a genuine open question about the
# process. Section 4 ("questions to ask") must not hand these to a reader as if a retry couldn't
# fix them; they stay explained in Section 3's "Not checked" bucket only. "open" is excluded from
# this set on purpose: on a pre-Checker sop_draft.md every flag is "open" and genuinely is the
# real list of questions at that point in the pipeline, so it still belongs in Section 4 there.
_INFRASTRUCTURE_FAILURE_STATUSES = {"unchecked", "check_failed"}


def _reliability_warning(doc: SOPDocument) -> Optional[str]:
    """The Reviewer does two independent jobs in one call: naming transitions, and running the
    four ambiguity checks. When one comes back full and the other comes back empty, job 1 was
    skipped rather than honestly empty, and the structural flags this run produced (or failed to
    produce) are not trustworthy -- see docs/06-known-gaps.md. Both-sparse or both-rich means the
    instruments agree, and this returns None. Pure arithmetic over fields already on `doc`; no new
    model output required."""
    ordered_steps = sorted(doc.steps, key=lambda s: s.order)
    pair_count = max(len(ordered_steps) - 1, 0)
    if pair_count == 0:
        return None
    transition_count = len(doc.transitions)
    ambiguity_count = sum(1 for f in doc.flags if f.family == "ambiguity")
    if transition_count == 0 and ambiguity_count >= max(pair_count, 2):
        return (
            f"The reviewer produced {ambiguity_count} ambiguity flag(s) but named no transitions "
            f"at all across {pair_count} step pair(s). Structural findings in this run may be an "
            "artifact of the model skipping that check rather than a real reflection of the "
            "process -- treat the gaps below with extra caution."
        )
    return None


def render_sop_md(doc: SOPDocument, *, video_name: str) -> str:
    """Render a SOPDocument into markdown, in four sections: the workflow as spoken, what the
    pipeline assumed, what is still missing, and the questions that follow from that. The document
    is meant to stand alone -- nobody re-watches the recording to catch what a status string like
    `check_failed` glossed over, so every section states plainly what it is showing rather than
    relying on the reader to notice.

    Pure function of its input -- same doc always renders to the same string, which is what makes
    run-to-run diffing meaningful."""
    lines: List[str] = []

    lines.append(f"# SOP -- {video_name}")
    lines.append(f"Run: {doc.run_id}")
    lines.append("")

    if doc.timing_degraded:
        lines.append(
            "> **TIMING DEGRADED** -- per-segment transcript timing was not available for this "
            "run (the transcription provider returned only a single whole-transcript segment). "
            "Every `[said @t]` timestamp below is unreliable and should not be trusted as the "
            "actual moment something was said."
        )
        lines.append("")

    if doc.review_failed:
        lines.append(
            "> **REVIEW FAILED** -- the review stage did not complete for this run. Zero flags "
            "were produced. **This document has NOT been reviewed for gaps.** See run metrics "
            "for details."
        )
        lines.append("")

    if not doc.steps:
        lines.append("_No steps were extracted from this recording._")
        lines.append("")
        if doc.appendix_segments:
            lines.append("## Appendix -- segments not converted to steps")
            for seg in doc.appendix_segments:
                reason_suffix = f" -- {seg.reason}" if seg.reason else ""
                lines.append(f'- `@{fmt_ts(seg.t_s)}` "{seg.text}"{reason_suffix}')
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    flags_between: dict[tuple[str, str], list] = {}
    for flag in doc.flags:
        if flag.family == "structural" and flag.between_step_ids:
            flags_between.setdefault(flag.between_step_ids, []).append(flag)

    ordered_steps = sorted(doc.steps, key=lambda s: s.order)

    # ---- Section 1: the workflow, as spoken -------------------------------------------------
    lines.append("## 1. The workflow, as spoken")
    lines.append("")
    for step in ordered_steps:
        lines.append(f"### {step.order}. {step.title}")
        said_tags = " ".join(
            _render_attribution_tag(a) for a in step.attributions if a.kind == "said"
        )
        if said_tags:
            lines.append(f"{step.instruction}  {said_tags}".rstrip())
        else:
            # Nobody said this -- every attribution on this step is "inferred" (a Checker-
            # inserted structural step, see Section 2). Without an explicit marker this would
            # read identically to a narrated step, which is exactly the ambiguity this section
            # exists to avoid.
            lines.append(f"{step.instruction}  _(not narrated -- see Section 2)_")
        lines.append("")

    if doc.transitions or any(f.family == "structural" for f in doc.flags):
        lines.append("### Process flow")
        lines.append("```mermaid")
        lines.append("flowchart TD")
        for step in ordered_steps:
            lines.append(f'    {step.step_id}["{step.order}. {step.title}"]')
        covered = {(t.from_step_id, t.to_step_id) for t in doc.transitions}
        for idx in range(len(ordered_steps) - 1):
            a, b = ordered_steps[idx], ordered_steps[idx + 1]
            a_inserted = a.attributions and all(attr.kind == "inferred" for attr in a.attributions)
            b_inserted = b.attributions and all(attr.kind == "inferred" for attr in b.attributions)
            if a_inserted or b_inserted:
                # This edge touches a Checker-inserted step. `covered`/`flags_between` are keyed
                # on the Reviewer's original consecutive-pairs chain, which has no entry for a
                # pair involving a step that didn't exist until the Checker spliced it in -- so
                # this edge would otherwise always fall through to the generic "-.gap.->" label,
                # regardless of whether the original pair was actually resolved. It always is:
                # inserting the step IS the resolution of the structural gap that used to sit
                # here, so render it solid and name the flag that explains the insertion, rather
                # than showing a second, redundant "gap" mark right where the gap was just closed.
                inserted_step = a if a_inserted else b
                label = inserted_step.flag_ids[0] if inserted_step.flag_ids else "resolved"
                lines.append(f"    {a.step_id} -->|{label}| {b.step_id}")
            elif (a.step_id, b.step_id) in covered:
                lines.append(f"    {a.step_id} --> {b.step_id}")
            else:
                gap_flags = flags_between.get((a.step_id, b.step_id), [])
                label = gap_flags[0].flag_id if gap_flags else "gap"
                lines.append(f"    {a.step_id} -.{label}.-> {b.step_id}")
        lines.append("```")
        lines.append("")

    # ---- Section 2: what we assumed ----------------------------------------------------------
    lines.append("## 2. What we assumed")
    lines.append("")
    assumed_lines: List[str] = []
    for step in ordered_steps:
        inserted = any(a.kind == "inferred" for a in step.attributions)
        if inserted:
            assumed_lines.append(
                f"- **New step {step.order}. {step.title}** -- nobody narrated this step; it was "
                f"inserted from what the video showed. {step.instruction} "
                + " ".join(_render_attribution_tag(a) for a in step.attributions if a.kind == "inferred")
            )
        for clause in step.inferred_clauses:
            assumed_lines.append(
                f"- Step {step.order} ({step.title}): {clause.text} "
                f"`[inferred from screen @{fmt_ts(clause.t_s)}, {clause.flag_id}]`"
            )
    if assumed_lines:
        lines.extend(assumed_lines)
    else:
        lines.append("_Nothing was assumed -- every line above is exactly what was said._")
    lines.append("")

    # ---- Section 3: what is missing, or might be ---------------------------------------------
    lines.append("## 3. What is missing, or might be")
    lines.append("")
    warning = _reliability_warning(doc)
    if warning:
        lines.append(f"> **REVIEWER SIGNALS DISAGREE** -- {warning}")
        lines.append("")

    resolutions_by_flag = {r.flag_id: r for r in doc.resolutions}
    contradicted = [f for f in doc.flags if f.status == "contradicted"]
    unresolvable = [f for f in doc.flags if f.status == "unresolvable"]
    not_checked = [f for f in doc.flags if f.status in _NOT_CHECKED_STATUSES]

    if contradicted:
        lines.append("### Contradicted -- the screen disagreed with the narration")
        lines.append("")
        for flag in contradicted:
            res = resolutions_by_flag.get(flag.flag_id)
            lines.append(f"- **{flag.flag_id}** ({flag.question})")
            if res:
                lines.append(f"  - Narration said: {res.narration_version}")
                lines.append(f"  - Screen showed: {res.screen_version}")
        lines.append("")

    if unresolvable:
        lines.append("### Unresolvable -- checked against the video, still unanswered")
        lines.append("")
        for flag in unresolvable:
            res = resolutions_by_flag.get(flag.flag_id)
            reason = f" -- {res.reason}" if res and res.reason else ""
            lines.append(f"- **{flag.flag_id}** ({flag.question}){reason}")
        lines.append("")

    if not_checked:
        lines.append(
            "### Not checked -- a pipeline limitation, not a confirmed gap in the process"
        )
        lines.append("")
        for flag in not_checked:
            lines.append(f"- **{flag.flag_id}** ({flag.question}) -- {_NOT_CHECKED_LABELS[flag.status]}")
        lines.append("")

    if not (contradicted or unresolvable or not_checked):
        lines.append("_Nothing is missing -- every open question was resolved from the video._")
        lines.append("")

    # ---- Section 4: questions to ask -----------------------------------------------------------
    lines.append("## 4. Questions to ask")
    lines.append("")
    open_flags = sorted(
        (
            f
            for f in doc.flags
            if f.status != "resolved" and f.status not in _INFRASTRUCTURE_FAILURE_STATUSES
        ),
        key=lambda f: _SEVERITY_ORDER.get(f.severity, 3),
    )
    if open_flags:
        for i, flag in enumerate(open_flags, start=1):
            where = f" (step {flag.step_id})" if flag.step_id else ""
            if flag.window_time_range:
                where += f" @{fmt_ts(flag.window_time_range[0])}"
            elif flag.window_segment_ids:
                where += f" @segment {min(flag.window_segment_ids)}"
            lines.append(f"{i}. [{flag.severity}] {flag.question}{where} `[{flag.flag_id}]`")
    elif any(f.status in _INFRASTRUCTURE_FAILURE_STATUSES for f in doc.flags):
        lines.append(
            "_No open questions right now -- everything else the reviewer flagged was resolved. "
            "Some flags could not be checked this run (a pipeline limitation, not a finding); "
            "see Section 3._"
        )
    else:
        lines.append("_No open questions -- everything the reviewer flagged was resolved._")
    lines.append("")

    if doc.appendix_segments:
        lines.append("## Appendix -- segments not converted to steps")
        for seg in doc.appendix_segments:
            reason_suffix = f" -- {seg.reason}" if seg.reason else ""
            lines.append(f'- `@{fmt_ts(seg.t_s)}` "{seg.text}"{reason_suffix}')
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_sop(doc: SOPDocument, run_dir: Path, *, video_name: str, filename: str = "sop.md") -> Path:
    """Render and persist sop.md (Phase 1: filename="sop_draft.md"; Phase 3 re-calls this as
    "sop.md" after the Checker's merge-back, giving a free before/after diff on disk)."""
    path = run_dir / filename
    path.write_text(render_sop_md(doc, video_name=video_name), encoding="utf-8")
    return path


def write_sop_json(doc: SOPDocument, run_dir: Path) -> Path:
    return save_json(doc.model_dump(), run_dir / "sop.json")
