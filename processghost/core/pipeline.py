"""End-to-end pipeline orchestrator: the full Writer -> Reviewer -> Checker chain.

v2 design: see Process_ghost/processghost-v2-writer-reviewer-checker-plan.md and
docs/14-writer-reviewer-checker.md. `run_pipeline`: transcript -> one LLM call (Writer) -> a
validated SOPDocument -> one LLM call (Reviewer, transitions/flags merged in) -> per-flag Checker
calls (screen-event evidence -> resolutions merged back in) -> sop.md. The Writer is the one FATAL stage
-- unlike every other LLM call site here, it has no per-item fallback, because there is no
meaningful document to fall back to; on any failure (WriterFailedError) the run aborts, after
recording the failure on the run's metrics. The Reviewer is non-fatal: on any failure it ships the
Writer's document unchanged with review_failed=True and zero flags. The Checker is non-fatal per
flag: a single flag's call failing marks that flag status="check_failed" and the run continues.

The v1 pipeline (core/align/, core/rules/, the intent/action/cross-check prompts and parsers) was
deleted once the Checker replaced it end-to-end -- see git history for that code if it's ever
needed for reference.

Provider switch (config.LLM_PROVIDER): "openai" (default) uses LangChain's ChatOpenAI and
"anthropic" uses ChatAnthropic (both built by core/llm/client.py: LLMClient.structured_chat)
for Writer/Reviewer/Checker reasoning. Transcription is always OpenAI Whisper --
Claude has no speech-to-text. Both providers expose the same structured_chat(...) -> BaseModel
interface, so _write_sop/_review_sop/_run_checker are provider-agnostic.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

from core import config
from core.io import storage
from core.io import video as video_io
from core.llm.client import LLMClient, Metrics, persist_metrics, persist_run_json
from core import retention
from core.guards import validate_input, validate_workbook
from core.manifest import build_manifest
from core.llm import prompts
from core.llm.parsing import (
    build_sop_from_writer_output,
    merge_reviewer_output,
)
from core.models.sop import (
    Attribution,
    CheckerOutput,
    Flag,
    FrameDescription,
    InferredClause,
    Resolution,
    ReviewerOutput,
    RunArtifacts,
    SOPDocument,
    SOPStep,
    WriterOutput,
)
from core.models.tokens import TranscriptSegment
from core.text_grounding import echo_score
from core.screen.align import align_events
from core.models.reconcile import ReconcilerOutput
from core.screen.evidence import build_evidence, select_events
from core.screen.ocr import get_ocr
from core.screen.reconcile import build_reconciler_input, merge_reconciler_output
from core.models.screen import ChangeRecord, ContextMap, Investigation, SelectionAnchor, UnusedEvent
from core.screen.anchors import build_anchors
from core.screen.cell_trail import build_trail
from core.screen.classify import classify_records, sent_to_reconciler, unused_from_classes
from core.screen.read_fallback import repair_records
from core.screen.slot_fills import fill_slots_from_anchors
from core.data.answer import deterministic_outcomes, enrich_slots
from core.data.apply import apply_outcome, attach_attempt, skipped_attempt
from core.data.checker import check_flag_with_data
from core.data.guard import guard_claims
from core.data.match import check_match, frame_texts
from core.data.params import add_parameters
from core.data.repeat import add_repeat_questions
from core.data.profile import profile_workbook
from core.data.tools import WorkbookTools
from core.ladder import record_video_rungs, route_to_document
from core.screen.context import build_contexts
from core.screen.investigate import (
    attach_evidence_ids,
    build_investigations,
    coverage_of,
    explained_event_ids,
    update_status,
)
from core.screen.records import build_change_records, cached_ocr
from core.screen.speech_clue import attach_speech_clues
from core.screen.timeline import build_screen_timeline
from report import sop_render
from report.build_spec import write_build_spec
from report.flow import write_flow


def _make_client(metrics: Metrics):
    """Provider factory -- the only place run_pipeline branches on config.LLM_PROVIDER for client
    construction. One LLMClient serves both providers, so
    _write_sop/_review_sop/the Checker work unchanged regardless of which one is active."""
    return LLMClient(metrics=metrics)


def _transcribe(client, audio_path: Path) -> list[dict]:
    """Transcription (OpenAI Whisper) -> segments_raw: list[{"id", "start", "end", "text"}]."""
    transcript_resp = client.transcribe(audio_path)
    segments = transcript_resp.get("segments", [])
    _attach_words(segments, transcript_resp.get("words") or [])
    return segments


def _attach_words(segments: list[dict], words: list[dict]) -> None:
    """Give each segment the words spoken inside it (`{"word", "start", "end"}`), so later stages can
    tell WHEN each word was said, not just which sentence. Words with no containing segment attach to
    the nearest earlier one. Segments are mutated in place; no words (older cache) leaves
    them as they were."""
    if not words or not segments:
        return
    for seg in segments:
        seg["words"] = []
    for w in words:
        owner = next((s for s in segments if s["start"] <= w["start"] < s["end"]), None)
        if owner is None:
            owner = max((s for s in segments if s["start"] <= w["start"]), key=lambda s: s["start"], default=segments[0])
        owner["words"].append({"word": w["word"], "start": float(w["start"]), "end": float(w["end"])})


class WriterFailedError(RuntimeError):
    """Raised when the Writer stage cannot produce a valid SOPDocument -- the one FATAL stage in
    this pipeline. Unlike every other LLM call site in the (eventual) v2 pipeline, there is no
    fallback here: a run with no document has nothing for the Reviewer or Checker to work with, so
    the whole run aborts rather than shipping a degraded stand-in."""


def _write_sop(client, run_id: str, segments_raw: list[dict]) -> SOPDocument:
    """Draft the SOP from the whole transcript in a single structured_chat call (see
    prompts.WRITER_SYSTEM -- same whole-transcript-at-once reasoning as the v1 pipeline's intent
    extraction: giving the model everything at once lets steps span multiple segments and vice
    versa in a way a per-segment call structurally cannot).

    On ANY failure -- the call itself, output that doesn't validate against WriterOutput, a missing
    segment, or a provenance violation caught inside build_sop_from_writer_output -- records exactly
    one failure on the run's metrics and raises WriterFailedError. No fallback: see the class
    docstring above.
    """
    try:
        numbered = [
            {
                "segment_index": i,
                "start_s": seg.get("start"),
                "end_s": seg.get("end"),
                "text": seg.get("text", ""),
            }
            for i, seg in enumerate(segments_raw)
        ]
        output = client.structured_chat(prompts.WRITER_SYSTEM, str(numbered), WriterOutput, stage="writer")
        return build_sop_from_writer_output(output, run_id=run_id, segments=segments_raw)
    except Exception as exc:  # noqa: BLE001 - the one place a pipeline stage is allowed to fail the run
        client.metrics.record_failure("writer", str(exc), meta={"segment_count": len(segments_raw)})
        raise WriterFailedError(str(exc)) from exc


def _review_sop(client, doc: SOPDocument, segments_raw: list[dict]) -> SOPDocument:
    """Review the drafted SOP in a single structured_chat call (see prompts.REVIEWER_SYSTEM): the
    model walks every consecutive step pair and names what carries between them where it honestly
    can, and separately flags ambiguous references. merge_reviewer_output then runs the coverage
    check -- any consecutive pair the model left uncovered becomes a synthesized structural flag --
    and returns doc with transitions/flags merged in. `segments_raw` is passed through so
    merge_reviewer_output can verify each transition's "quote" actually occurs in the transcript
    before counting it as coverage -- see docs/18-known-gaps-v2.md entry 1 for why silence alone
    was not a reliable signal.

    Non-fatal, unlike the Writer: on any failure this returns doc unchanged except for
    review_failed=True and records exactly one failure on the run's metrics. A run with a valid
    Writer document but a failed review still ships -- see sop_render's "REVIEW FAILED" banner --
    rather than throwing away a document that has nothing wrong with it.
    """
    try:
        numbered_steps = [
            {
                "step_id": s.step_id,
                "order": s.order,
                "title": s.title,
                "instruction": s.instruction,
            }
            for s in sorted(doc.steps, key=lambda s: s.order)
        ]
        numbered_segments = [
            {
                "segment_index": i,
                "start_s": seg.get("start"),
                "end_s": seg.get("end"),
                "text": seg.get("text", ""),
            }
            for i, seg in enumerate(segments_raw)
        ]
        user_text = str({"steps": numbered_steps, "transcript": numbered_segments})
        output = client.structured_chat(prompts.REVIEWER_SYSTEM, user_text, ReviewerOutput, stage="reviewer")
        return merge_reviewer_output(output, doc, segments=segments_raw)
    except Exception as exc:  # noqa: BLE001 - Reviewer failure must degrade, not fail the run
        client.metrics.record_failure("reviewer", str(exc), meta={"step_count": len(doc.steps)})
        return doc.model_copy(update={"review_failed": True})


def _select_flags_for_check(doc: SOPDocument, skip_slots: Tuple[str, ...] = ()) -> Tuple[List[Flag], List[Flag]]:
    """Sort open flags by severity (blocking first per the original plan) and split at
    config.MAX_CHECKED_FLAGS. The trailing half is never silently dropped -- run_checker marks
    each one status="unchecked" rather than leaving it "open" and indistinguishable from a flag
    that was never even considered."""
    severity_order = {"blocking": 0, "caution": 1, "note": 2}
    open_flags = sorted(
        (f for f in doc.flags if f.status == "open" and f.slot not in skip_slots), key=lambda f: severity_order.get(f.severity, 3)
    )
    return open_flags[: config.MAX_CHECKED_FLAGS], open_flags[config.MAX_CHECKED_FLAGS :]


def _fallback_window_from_steps(flag: Flag, doc: SOPDocument) -> Tuple[float, float]:
    """Used when a flag has no window_segment_ids/window_time_range to derive a time range from --
    anchors on the flag's step (ambiguity) or the first of its two bounding steps (structural, from
    the Reviewer's coverage-check synthesis), then widens by CHECKER_FALLBACK_WINDOW_S."""
    by_id = {s.step_id: s for s in doc.steps}
    anchor_id = flag.step_id or (flag.between_step_ids[0] if flag.between_step_ids else None)
    step = by_id.get(anchor_id) if anchor_id else None
    t0 = step.attributions[0].t_s if step and step.attributions else 0.0
    return t0, t0 + config.CHECKER_FALLBACK_WINDOW_S


def _flag_window_s(flag: Flag, doc: SOPDocument, transcript: List[TranscriptSegment]) -> Tuple[float, float]:
    """Resolve a flag's frame-extraction window in seconds: window_time_range if the flag has one,
    else the min/max timestamps of its window_segment_ids, else the step-anchored fallback above.
    Then apply the lead-in and minimum-width config the same way for every flag."""
    if flag.window_time_range:
        start, end = flag.window_time_range
    else:
        ids = [i for i in flag.window_segment_ids if 0 <= i < len(transcript)]
        if ids:
            start = min(transcript[i].start_s for i in ids)
            end = max(transcript[i].end_s for i in ids)
        else:
            start, end = _fallback_window_from_steps(flag, doc)

    start = max(0.0, start - config.CHECKER_LEAD_IN_S)
    if end - start < config.CHECKER_MIN_WINDOW_S:
        end = start + config.CHECKER_MIN_WINDOW_S
    return start, end


def _flag_step_excerpt(doc: SOPDocument, flag: Flag) -> List[dict]:
    ids = set()
    if flag.step_id:
        ids.add(flag.step_id)
    if flag.between_step_ids:
        ids.update(flag.between_step_ids)
    return [
        {"step_id": s.step_id, "order": s.order, "title": s.title, "instruction": s.instruction}
        for s in doc.steps
        if s.step_id in ids
    ]


def _flag_transcript_excerpt(flag: Flag, transcript: List[TranscriptSegment]) -> List[dict]:
    return [
        {"t_s": transcript[i].start_s, "text": transcript[i].text}
        for i in flag.window_segment_ids
        if 0 <= i < len(transcript)
    ]


def _apply_resolution(doc: SOPDocument, flag: Flag, output: CheckerOutput, frame_paths: List[Path]) -> SOPDocument:
    """Merge one Checker verdict into `doc` -- the augment-don't-rewrite rule from docs/15 plus
    your call on missing steps: a resolved AMBIGUITY flag gets an InferredClause appended to its
    existing step (narrated text stays byte-stable); a resolved STRUCTURAL flag gets a brand new
    SOPStep inserted between its two bounding steps, carrying only an "inferred" attribution since
    nothing was said. `order` is renumbered by final list position afterward -- step_id is never
    renumbered, so no other flag's step_id/between_step_ids reference can go stale.
    """
    t_s = video_io.frame_timestamp_from_name(frame_paths[0].name) if frame_paths else 0.0
    evidence = (
        [FrameDescription(frame_path=fp, t_s=video_io.frame_timestamp_from_name(fp.name)) for fp in frame_paths]
        if output.verdict in ("resolved", "contradicted")
        else []
    )
    reason = output.reason or ("nothing on screen settled this question" if output.verdict == "unresolvable" else "")
    resolution = Resolution(
        flag_id=flag.flag_id,
        verdict=output.verdict,
        inferred_text=output.inferred_text,
        evidence=evidence,
        reason=reason,
        narration_version=output.narration_version,
        screen_version=output.screen_version,
    )

    steps = list(doc.steps)
    flags = list(doc.flags)
    idx = next(i for i, f in enumerate(flags) if f.flag_id == flag.flag_id)
    new_status = flags[idx].status
    new_severity = flags[idx].severity

    if output.verdict == "resolved":
        new_status = "resolved"
        if flag.family == "ambiguity" and flag.step_id:
            clause = InferredClause(text=output.inferred_text or "", t_s=t_s, frame_paths=frame_paths, flag_id=flag.flag_id)
            for i, s in enumerate(steps):
                if s.step_id == flag.step_id:
                    steps[i] = s.model_copy(
                        update={
                            "inferred_clauses": s.inferred_clauses + [clause],
                            "flag_ids": s.flag_ids + [flag.flag_id],
                        }
                    )
                    break
        elif flag.family == "structural" and flag.between_step_ids:
            from_id, _to_id = flag.between_step_ids
            new_step = SOPStep(
                step_id=f"step-ins-{flag.flag_id}",
                order=0,  # placeholder; every step's order is renumbered by final position below
                title=(output.inferred_text or "Inferred step")[:60],
                instruction=output.inferred_text or "",
                attributions=[Attribution(kind="inferred", t_s=t_s, frame_paths=frame_paths)],
                flag_ids=[flag.flag_id],
            )
            insert_at = next((i for i, s in enumerate(steps) if s.step_id == from_id), len(steps) - 1) + 1
            steps.insert(insert_at, new_step)
    elif output.verdict == "contradicted":
        new_status = "contradicted"
        new_severity = "blocking"
    elif output.verdict == "unresolvable":
        new_status = "unresolvable"  # a distinct terminal status, not the same as never-checked

    flags[idx] = flags[idx].model_copy(update={"status": new_status, "severity": new_severity})
    steps = [s.model_copy(update={"order": i + 1}) for i, s in enumerate(steps)]
    return doc.model_copy(update={"steps": steps, "flags": flags, "resolutions": doc.resolutions + [resolution]})


def _stamp_flag(doc: SOPDocument, flag_id: str, tier: str, level: int) -> SOPDocument:
    flags = [
        f.model_copy(update={"checker_tier": tier, "expansion_level": level}) if f.flag_id == flag_id else f
        for f in doc.flags
    ]
    return doc.model_copy(update={"flags": flags})


def _downgrade_echo(output: CheckerOutput, prompt_text: str) -> CheckerOutput:
    """A `resolved` verdict whose observation mostly restates the prompt rather than the evidence
    (docs/18 entry 5) becomes `unresolvable` -- a restated prompt cannot support a resolution."""
    if output.verdict != "resolved":
        return output
    echo = echo_score(output.observed, prompt_text)
    if echo is None or echo < config.CHECKER_ECHO_MAX:
        return output
    return output.model_copy(
        update={
            "verdict": "unresolvable",
            "inferred_text": None,
            "reason": "the observation restates the question, step or transcript rather than describing the evidence",
        }
    )


def _check_flag_with_events(
    client,
    doc: SOPDocument,
    flag: Flag,
    transcript: List[TranscriptSegment],
    timeline,
    timeline_dir: Path,
    evidence_dir: Path,
    ocr,
) -> SOPDocument:
    """v3 Checker path (docs/22): evidence is built from the screen events attached to the flag
    instead of fixed-interval frames. Level 0 sends the nearest event as OCR text + a text model;
    if that is unresolvable, level 1 widens to +/-SCREEN_ESCALATION_EVENTS events and attaches the images. At most 2 calls per flag, and the model never
    decides how much context it gets. On ANY failure the flag gets
    status="check_failed" and the run continues; a single flag's failure never aborts the run."""
    try:
        w0, w1 = _flag_window_s(flag, doc, transcript)
        context = {
            "question": flag.question,
            "family": flag.family,
            "step_excerpt": _flag_step_excerpt(doc, flag),
            "transcript_excerpt": _flag_transcript_excerpt(flag, transcript),
        }
        prompt_text = str(context)
        output, pack, tier, level = None, None, "ocr", 0
        for level in (0, 1):
            events = select_events(flag.event_ids, timeline.events, (w0 + w1) / 2.0, level)
            if not events:
                raise RuntimeError("no screen events attached to this flag")
            pack = build_evidence(
                events, timeline_dir, evidence_dir / flag.flag_id / f"level{level}", ocr, video_sha=timeline.video_sha
            )
            if not pack.after_frames:
                raise RuntimeError("no readable keyframes for this flag's events")
            use_images = level == 1 and bool(pack.images)
            tier = "vision" if use_images else "ocr"
            user_text = (
                prompt_text
                + "\n\nEvidence:\n"
                + pack.description
                + ("\n\nExtracted screen text:\n" + pack.ocr_text if pack.ocr_text else "")
            )
            output = client.structured_chat(
                prompts.CHECKER_EVENT_SYSTEM,
                user_text,
                CheckerOutput,
                images=pack.images if use_images else None,
                stage="checker",
                payload_extra={"flag_id": flag.flag_id, "level": level, "tier": tier, "evidence_ids": list(pack.evidence_ids)},
            )
            output = _downgrade_echo(output, prompt_text)
            if output.verdict != "unresolvable":
                break
        doc = _apply_resolution(doc, flag, output, pack.after_frames)
        return _stamp_flag(doc, flag.flag_id, tier, level)
    except Exception as exc:  # noqa: BLE001 - one flag's failure must degrade, not fail the run
        client.metrics.record_failure("checker", str(exc), meta={"flag_id": flag.flag_id, "path": "events"})
        flags = [
            f.model_copy(update={"status": "check_failed"}) if f.flag_id == flag.flag_id else f
            for f in doc.flags
        ]
        return doc.model_copy(update={"flags": flags})


def _run_checker(
    client,
    doc: SOPDocument,
    transcript: List[TranscriptSegment],
    checker_frames_dir: Path,
    timeline=None,
    timeline_dir: Optional[Path] = None,
    ocr=None,
    skip_slots: Tuple[str, ...] = (),
) -> SOPDocument:
    """Phase 3: resolve every open flag, severity-first, up to MAX_CHECKED_FLAGS. A flag is checked
    from the screen events attached to it (_check_flag_with_events). A flag with no screen events,
    or any flag when there is no screen timeline, has no evidence to check against and is marked
    status="unchecked" -- never silently dropped.
    """
    checked, unchecked = _select_flags_for_check(doc, skip_slots)
    if not checked and not unchecked:
        return doc

    checker_frames_dir.mkdir(parents=True, exist_ok=True)
    no_events = [f for f in checked if timeline is None or not f.event_ids]
    checked = [f for f in checked if f not in no_events]
    unchecked = no_events + list(unchecked)

    for i, flag in enumerate(checked):
        if client.metrics.over_budget():
            # Stop starting new flags; the rest are marked "unchecked" below, same as the flag cap.
            client.metrics.record_failure(
                "budget_exceeded",
                f"estimated spend ${client.metrics.cost_usd:.2f} reached MAX_RUN_COST_USD ${config.MAX_RUN_COST_USD:.2f}",
                meta={"flags_not_checked": len(checked) - i},
            )
            unchecked = list(checked[i:]) + list(unchecked)
            break
        doc = _check_flag_with_events(
            client, doc, flag, transcript, timeline, timeline_dir, checker_frames_dir / "events", ocr
        )

    if unchecked:
        unchecked_ids = {f.flag_id for f in unchecked}
        flags = [
            f.model_copy(update={"status": "unchecked"}) if f.flag_id in unchecked_ids else f
            for f in doc.flags
        ]
        doc = doc.model_copy(update={"flags": flags})

    return doc


def _screen_stage(client, run_dir: Path, video_path: Path):
    """Stage 0b: build the screen timeline. Non-fatal -- on any failure records `screen_failed` and
    returns None, and the run continues with v2 behaviour (no silent-step flags, interval frames)."""
    try:
        timeline = build_screen_timeline(video_path, run_dir / "screen")
        (run_dir / "screen" / "screen_events.json").write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
        return timeline
    except Exception as exc:  # noqa: BLE001 - a broken diff must not lose the run
        client.metrics.record_failure("screen_failed", str(exc))
        return None


def _align_stage(
    client, run_dir: Path, doc: SOPDocument, timeline, transcript: List[TranscriptSegment], *, silent_flags: bool = True
) -> SOPDocument:
    """Stage 2b: silent-step flags + event attachment. Non-fatal -- on failure records
    `align_failed` and returns the document unchanged."""
    try:
        result = align_events(doc, timeline.events, transcript, silent_flags=silent_flags)
        storage.save_json(
            {"records": result.records, "said_no_event_step_ids": result.said_no_event_step_ids},
            run_dir / "alignment.json",
        )
        return result.doc
    except Exception as exc:  # noqa: BLE001
        client.metrics.record_failure("align_failed", str(exc))
        return doc


class SurfaceResult(NamedTuple):
    timeline: object
    records: List[ChangeRecord]
    anchors: List[SelectionAnchor]
    unused: List[UnusedEvent]
    contexts: List[ContextMap]


def _surface_stage(client, run_dir: Path, timeline, segments_raw: list[dict]) -> Optional[SurfaceResult]:
    """Stage 0c (ADR 0013, Trigger A): change records with surface locators, unread selected-cell fields
    repaired (retry with a changed input, then one batched cheap-model read), event classes by state
    change, selection anchors joined to speech and later values by time, speech clues, and the context
    map. Non-fatal -- returns None on any failure (recorded as `surface_failed`) and the run continues
    without locators, exactly as before this stage existed."""
    try:
        ocr = get_ocr()
        if ocr is None:
            raise RuntimeError("no local OCR engine available (pip install rapidocr-onnxruntime)")
        screen_dir = run_dir / "screen"
        records = build_change_records(timeline, screen_dir, ocr)
        records, read_stats = repair_records(records, timeline, screen_dir, cached_ocr(ocr), client, screen_dir / "reads")
        client.metrics.record_payload("read_fallback", dict(read_stats))
        records = classify_records(attach_speech_clues(records, segments_raw))
        anchors, unused_selections = build_anchors(records, segments_raw)
        contexts, timeline, records = build_contexts(timeline, records, screen_dir, cached_ocr(ocr))
        (screen_dir / "screen_events.json").write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
        storage.save_json([r.model_dump(mode="json") for r in records], run_dir / "change_records.json")
        storage.save_json([c.model_dump(mode="json") for c in contexts], run_dir / "contexts.json")
        storage.save_json([a.model_dump(mode="json") for a in anchors], run_dir / "anchors.json")
        return SurfaceResult(timeline, records, anchors, unused_from_classes(records) + unused_selections, contexts)
    except Exception as exc:  # noqa: BLE001 - locators are additive; their failure must not lose the run
        client.metrics.record_failure("surface_failed", str(exc))
        return None


def _document_stage(
    client,
    run_dir: Path,
    doc: SOPDocument,
    workbook: Path,
    records: Optional[List[ChangeRecord]] = None,
    timeline=None,
    contexts: Optional[List[ContextMap]] = None,
) -> SOPDocument:
    """The document rung (docs/26 flow plan): profile the workbook with no model, then let a short loop of
    read-only lookups try the questions the video left open (and cross-check blocking ones the video resolved).
    Non-fatal: any failure is recorded as `document_failed` and the document comes back unchanged. The
    workbook is opened as a copy and its hash is checked again at the end."""
    try:
        tools = WorkbookTools(workbook, run_dir / "workbook")
        profile = profile_workbook(tools.copy_path).model_copy(update={"workbook": tools.name, "sha256": tools.sha256_original})
        storage.save_json(profile.model_dump(mode="json"), run_dir / "workbook_profile.json")
        trail = build_trail(records or [], [s.name for s in profile.sheets])
        storage.save_json([t.model_dump(mode="json") for t in trail], run_dir / "cell_trail.json")

        # Is this the workbook that was open in the recording? Answers drawn from a different file would be about the wrong file.
        ocr = get_ocr() if records and timeline is not None and contexts else None
        texts = frame_texts(contexts, timeline, run_dir / "screen", cached_ocr(ocr)) if ocr is not None else {}
        match = check_match(records or [], trail, texts, tools)  # "unknown" when there is nothing from the recording to compare
        doc = doc.model_copy(update={"workbook_match": match})
        storage.save_json(match.model_dump(mode="json"), run_dir / "workbook_match.json")
        if match.verdict == "mismatch":
            client.metrics.record_failure("workbook_mismatch", "; ".join(i.detail for i in match.issues))
            routed, overflow = route_to_document(doc, True)
            for flag in routed + overflow:
                doc = attach_attempt(doc, flag.flag_id, skipped_attempt("not checked: the workbook does not match the recording"))
            return doc

        # A number in a video claim must belong to a cell that depends on something this recording changed.
        doc = guard_claims(enrich_slots(doc, trail, tools), trail, tools)
        # What code can answer exactly is answered without a model: the cells that were used are known, the formulas that read
        # them and the columns that carry the same value on two sheets are facts in the file.
        for flag_id, outcome in deterministic_outcomes(doc, trail, profile, tools).items():
            doc = apply_outcome(doc, flag_id, outcome)
        doc = add_parameters(doc, trail, profile, tools)  # which values probably change each run, and what would break next time
        doc = add_repeat_questions(doc, trail, tools)  # the recording shows one item; the workbook shows how many the process covers
        observed = [{"sheet": t.sheet, "cell": t.cell, "content": t.content} for t in trail]
        routed, overflow = route_to_document(doc, True)
        for flag in overflow:
            doc = attach_attempt(doc, flag.flag_id, skipped_attempt("not routed: over this run's document-rung question cap"))
        start_cost = client.metrics.cost_usd
        for flag in routed:
            if client.metrics.over_budget() or client.metrics.cost_usd - start_cost >= config.DATA_PASS_BUDGET_USD:
                client.metrics.record_failure("document_budget", "document-rung spend limit reached", meta={"flag_id": flag.flag_id})
                doc = attach_attempt(doc, flag.flag_id, skipped_attempt("not checked: the document-rung spend limit was reached"))
                continue
            try:
                doc = apply_outcome(doc, flag.flag_id, check_flag_with_data(client, flag, doc, profile, tools, observed=observed))
            except Exception as exc:  # noqa: BLE001 - one question failing must not lose the others
                client.metrics.record_failure("document_check_failed", str(exc), meta={"flag_id": flag.flag_id})
                doc = attach_attempt(doc, flag.flag_id, skipped_attempt("the document check itself failed"))
        if not tools.verify_unchanged():
            client.metrics.record_failure("workbook_changed", "the source workbook's hash changed during the run")
        return doc
    except Exception as exc:  # noqa: BLE001 - the document rung is additive; its failure must not lose the run
        client.metrics.record_failure("document_failed", str(exc))
        return doc


def _investigate_stage(client, run_dir: Path, timeline, records, doc: SOPDocument, transcript) -> Optional[List[Investigation]]:
    """Merge the three triggers into investigations (ADR 0011). Non-fatal; statuses are filled in by
    `_finish_investigations` once the Checker has run."""
    try:
        align_path = run_dir / "alignment.json"
        align_records = json.loads(align_path.read_text(encoding="utf-8")).get("records", []) if align_path.exists() else []
        explained = explained_event_ids(doc, align_records)
        return build_investigations(timeline, records or [], doc, transcript, explained)
    except Exception as exc:  # noqa: BLE001
        client.metrics.record_failure("investigate_failed", str(exc))
        return None


def _finish_investigations(client, run_dir: Path, invs: List[Investigation], doc: SOPDocument, timeline) -> SOPDocument:
    """After the Checker: record what was and was not examined, in investigations.json and on the SOP."""
    align_path = run_dir / "alignment.json"
    align_records = json.loads(align_path.read_text(encoding="utf-8")).get("records", []) if align_path.exists() else []
    explained = explained_event_ids(doc, align_records)
    budget_exceeded = any(f.get("stage") == "budget_exceeded" for f in client.metrics.failures)
    invs = attach_evidence_ids(update_status(invs, doc, explained, budget_exceeded=budget_exceeded), timeline)
    storage.save_json([v.model_dump(mode="json") for v in invs], run_dir / "investigations.json")
    return doc.model_copy(update={"screen_coverage": coverage_of(invs)})


def _reconcile_stage(
    client,
    run_dir: Path,
    doc: SOPDocument,
    timeline,
    segments_raw: list[dict],
    transcript: List[TranscriptSegment],
    records: Optional[List[ChangeRecord]] = None,
    anchors: Optional[List[SelectionAnchor]] = None,
) -> Optional[SOPDocument]:
    """Stage 2c (docs/24): one batched call mapping steps onto screen events and saying what changed
    for every step. Returns None on any failure (recorded as `reconcile_failed`) so the caller falls
    back to Align's counting heuristic. `records` come from the surface stage when it ran."""
    try:
        if records is None:
            ocr = get_ocr()
            if ocr is None:
                raise RuntimeError("no local OCR engine available (pip install rapidocr-onnxruntime)")
            records = build_change_records(timeline, run_dir / "screen", ocr)
            storage.save_json([r.model_dump(mode="json") for r in records], run_dir / "change_records.json")
        output = client.structured_chat(
            prompts.RECONCILER_SYSTEM,
            build_reconciler_input(doc, records, segments_raw, anchors),
            ReconcilerOutput,
            stage="reconcile",
        )
        return merge_reconciler_output(doc, output, records, transcript)
    except Exception as exc:  # noqa: BLE001 - the reconciler is additive; its failure must not lose the run
        client.metrics.record_failure("reconcile_failed", str(exc))
        return None


def run_pipeline(
    video_file: Path,
    frame_interval_s: float = config.FRAME_INTERVAL_S,
    *,
    use_screen: bool = False,
    reconcile: bool = True,
    workbook: Optional[Path] = None,
) -> RunArtifacts:
    """Phase 1+2: audio extraction -> STT -> Writer -> Reviewer -> sop_draft.md. Checker (Phase 3)
    is not implemented yet; frame_interval_s is accepted for API compatibility with the Streamlit
    UI and future phases but is currently only used for the sparse frame pass, which nothing
    downstream consumes yet.
    """
    validate_input(video_file)  # refuse before any file is copied or any call is made
    if workbook is not None:
        validate_workbook(workbook)
    t0 = time.time()
    metrics = Metrics()
    client = _make_client(metrics)

    run_dir = storage.new_run_dir()
    # Set before any LLM call happens so every call from here on appends to the trace immediately
    # (crash-safe) rather than only being visible if the run makes it to persist_run_json at the end.
    metrics.trace_path = run_dir / "trace.jsonl"
    metrics.run_id = run_dir.name
    local_video = run_dir / "video.mp4"
    shutil.copy(video_file, local_video)
    audio_path = run_dir / "audio.wav"
    storage.save_json(build_manifest(local_video), run_dir / "manifest.json")

    try:
        with metrics.stage("transcribe"):
            video_io.extract_audio(local_video, audio_path)
            segments_raw = _transcribe(client, audio_path)

        transcript = [
            TranscriptSegment(
                start_s=float(seg["start"]),
                end_s=float(seg["end"]),
                text=seg.get("text", ""),
                speaker=None,
            )
            for seg in segments_raw
        ]

        # Degraded-STT guard: a single segment spanning the whole transcript (start=0.0,
        # end=0.0) means per-chunk timing is unavailable. Every
        # [said @t] in the eventual doc would then read "@0:00" -- record it once here so the
        # provenance rule fails loudly (via the timing_degraded banner) rather than shipping a
        # document full of fake attributions.
        timing_degraded = len(transcript) == 1 and transcript[0].end_s == 0.0
        if timing_degraded:
            metrics.record_failure(
                "stt_timing_degraded",
                "STT returned only one whole-transcript segment; per-line timestamps are unreliable",
                meta={"segment_count": len(segments_raw)},
            )

        with metrics.stage("writer"):
            sop = _write_sop(client, run_dir.name, segments_raw)
        if timing_degraded:
            sop = sop.model_copy(update={"timing_degraded": True})

        with metrics.stage("reviewer"):
            sop = _review_sop(client, sop, segments_raw)

        # v3 (docs/22), opt-in: screen timeline + Align. Off by default so run_pipeline stays
        # byte-identical to v2 for every existing caller.
        timeline = None
        records: Optional[List[ChangeRecord]] = None
        contexts: Optional[List[ContextMap]] = None
        investigations: Optional[List[Investigation]] = None
        if use_screen and not timing_degraded:
            with metrics.stage("screen"):
                timeline = _screen_stage(client, run_dir, local_video)
            if timeline is not None:
                with metrics.stage("surface"):
                    surface = _surface_stage(client, run_dir, timeline, segments_raw)
                records: Optional[List[ChangeRecord]] = None
                anchors: List[SelectionAnchor] = []
                unused: List[UnusedEvent] = []
                if surface is not None:
                    timeline, records, anchors, unused, contexts = surface
                with metrics.stage("align"):
                    attached = _align_stage(client, run_dir, sop, timeline, transcript, silent_flags=not reconcile)
                reconciled = None
                if reconcile:
                    with metrics.stage("reconcile"):
                        reconciled = _reconcile_stage(
                            client, run_dir, attached, timeline, segments_raw, transcript,
                            records=sent_to_reconciler(records) if records is not None else None, anchors=anchors,
                        )
                    if reconciled is None:  # fall back to Align's counting heuristic for silent steps
                        attached = _align_stage(client, run_dir, sop, timeline, transcript, silent_flags=True)
                sop = reconciled if reconciled is not None else attached
                sop = sop.model_copy(update={"anchors": anchors, "screen_activity_unused": unused})
                sop = fill_slots_from_anchors(sop, anchors, records or [])
                investigations = _investigate_stage(client, run_dir, timeline, records, sop, transcript)

        sop_draft_path = sop_render.write_sop(
            sop, run_dir, video_name=video_file.name, filename="sop_draft.md"
        )

        checker_frames_dir = run_dir / "checker_frames"
        with metrics.stage("checker"):
            sop = _run_checker(
                client, sop, transcript, checker_frames_dir,
                timeline=timeline, timeline_dir=run_dir / "screen", ocr=get_ocr() if timeline is not None else None,
                # With a workbook, how a row is picked and what else reads a cell are answered from the file, not guessed from pixels.
                skip_slots=("key", "effect") if workbook is not None else (),
            )

        sop = record_video_rungs(sop)
        if workbook is not None:
            with metrics.stage("document"):
                sop = _document_stage(client, run_dir, sop, workbook, records, timeline, contexts)

        if investigations is not None:
            sop = _finish_investigations(client, run_dir, investigations, sop, timeline)

        sop_md_path = sop_render.write_sop(sop, run_dir, video_name=video_file.name, filename="sop.md")
        sop_render.write_sop_json(sop, run_dir)
        build_spec_path = write_build_spec(sop, run_dir)
        try:
            write_flow(sop, run_dir)
        except Exception as exc:  # noqa: BLE001 - the flow is a rendering of a finished document; its failure must not lose the run
            metrics.record_failure("flow_failed", str(exc))

        artifacts = RunArtifacts(
            run_id=run_dir.name,
            video_path=local_video,
            audio_path=audio_path,
            frames_dir=run_dir / "frames",
            checker_frames_dir=checker_frames_dir,
            transcript=transcript,
            sop=sop,
            sop_draft_md=sop_draft_path,
            sop_md=sop_md_path,
            build_spec_path=build_spec_path,
        )

        duration_s = time.time() - t0
        artifacts.metrics = metrics.to_dict() | {"duration_s": duration_s}
        persist_run_json(run_dir, artifacts.model_dump())
        return artifacts
    finally:
        # Persist metrics even on a fatal WriterFailedError abort, so a failed run is still
        # auditable from logs/run-metrics.csv.
        persist_metrics(run_dir.name, metrics, video_name=video_file.name, duration_s=time.time() - t0)
        if not config.KEEP_RUN_MEDIA:
            retention.strip_media(run_dir)


