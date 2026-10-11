"""End-to-end pipeline orchestrator: the full Writer -> Reviewer -> Checker chain.

v2 design: see Process_ghost/processghost-v2-writer-reviewer-checker-plan.md and
docs/14-writer-reviewer-checker.md. `run_pipeline`: transcript -> one LLM call (Writer) -> a
validated SOPDocument -> one LLM call (Reviewer, transitions/flags merged in) -> per-flag Checker
calls (frames/OCR text -> resolutions merged back in) -> sop.md. The Writer is the one FATAL stage
-- unlike every other LLM call site here, it has no per-item fallback, because there is no
meaningful document to fall back to; on any failure (WriterFailedError) the run aborts, after
recording the failure on the run's metrics. The Reviewer is non-fatal: on any failure it ships the
Writer's document unchanged with review_failed=True and zero flags. The Checker is non-fatal per
flag: a single flag's call failing marks that flag status="check_failed" and the run continues.

The v1 pipeline (core/align/, core/rules/, the intent/action/cross-check prompts and parsers) was
deleted once the Checker replaced it end-to-end -- see git history for that code if it's ever
needed for reference.

Provider switch (config.LLM_PROVIDER): "openai" uses Whisper + LangChain's ChatOpenAI (see
core/llm/client.py: structured_chat) for Writer/Reviewer/Checker reasoning, real vision per Checker
call. "sarvam" uses Saaras v3 (batch STT) + Sarvam-105B, with no true vision model -- the Checker's
Sarvam path OCRs every checked flag's frames in ONE batched document-intelligence job first, then
reasons over the extracted text per flag -- see core/llm/sarvam_client.py for the full reasoning
and what's confirmed vs. best-effort in that design. Both providers converge on the same
segments_raw shape after transcription and the same structured_chat(...) -> BaseModel interface
for every reasoning call, so _write_sop/_review_sop/_run_checker are provider-agnostic.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core import config
from core.io import storage
from core.io import video as video_io
from core.llm.client import LLMClient, Metrics, persist_metrics, persist_run_json
from core import retention
from core.guards import validate_input
from core.manifest import build_manifest
from core.llm import prompts
from core.llm.parsing import (
    build_sop_from_writer_output,
    merge_reviewer_output,
    parse_sarvam_stt_result,
)
from core.llm.sarvam_client import SarvamClient
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
from core.screen.records import build_change_records
from core.screen.timeline import build_screen_timeline
from report import sop_render
from report.build_spec import write_build_spec


def _make_client(metrics: Metrics):
    """Provider factory -- the only place run_pipeline branches on config.LLM_PROVIDER for client
    construction. Both client types expose a compatible .structured_chat(system_prompt, user_text,
    schema, images=None) -> BaseModel and a .metrics attribute (Phase 3: LangChain for OpenAI,
    JSON-mode-by-prompt for Sarvam -- see core/llm/client.py and core/llm/sarvam_client.py), so
    _write_sop/_review_sop/the Checker work unchanged regardless of which one is active."""
    if config.LLM_PROVIDER == "sarvam":
        return SarvamClient(metrics=metrics)
    return LLMClient(metrics=metrics)


def _transcribe(client, audio_path: Path) -> list[dict]:
    """Transcription -- shape differs by provider, both converge on segments_raw:
    list[{"id", "start", "end", "text"}], so everything after this is provider-agnostic."""
    if config.LLM_PROVIDER == "sarvam":
        stt_result = client.transcribe(audio_path)
        return parse_sarvam_stt_result(stt_result)
    transcript_resp = client.transcribe(audio_path)
    segments = transcript_resp.get("segments", [])
    _attach_words(segments, transcript_resp.get("words") or [])
    return segments


def _attach_words(segments: list[dict], words: list[dict]) -> None:
    """Give each segment the words spoken inside it (`{"word", "start", "end"}`), so later stages can
    tell WHEN each word was said, not just which sentence. Words with no containing segment attach to
    the nearest earlier one. Segments are mutated in place; no words (Sarvam path, older cache) leaves
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


def _select_flags_for_check(doc: SOPDocument) -> Tuple[List[Flag], List[Flag]]:
    """Sort open flags by severity (blocking first per the original plan) and split at
    config.MAX_CHECKED_FLAGS. The trailing half is never silently dropped -- run_checker marks
    each one status="unchecked" rather than leaving it "open" and indistinguishable from a flag
    that was never even considered."""
    severity_order = {"blocking": 0, "caution": 1, "note": 2}
    open_flags = sorted(
        (f for f in doc.flags if f.status == "open"), key=lambda f: severity_order.get(f.severity, 3)
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


def _check_one_flag(
    client,
    doc: SOPDocument,
    flag: Flag,
    transcript: List[TranscriptSegment],
    frame_paths: List[Path],
    ocr_text_by_frame: Dict[str, str],
) -> SOPDocument:
    """Resolve one flag: one structured_chat call (see prompts.CHECKER_SYSTEM), images for OpenAI
    or OCR text for Sarvam (no true vision model -- see core/llm/sarvam_client.py). On ANY failure
    -- no frames/OCR text available, the call itself, output that doesn't validate -- the flag gets
    status="check_failed" and the run continues; a single flag's failure never aborts the run."""
    try:
        user_text = str(
            {
                "question": flag.question,
                "family": flag.family,
                "step_excerpt": _flag_step_excerpt(doc, flag),
                "transcript_excerpt": _flag_transcript_excerpt(flag, transcript),
            }
        )
        if config.LLM_PROVIDER == "sarvam":
            ocr_text = "\n\n".join(
                ocr_text_by_frame[fp.name] for fp in frame_paths if ocr_text_by_frame.get(fp.name)
            )
            if not ocr_text:
                raise RuntimeError("no OCR text available for this flag's frames")
            output = client.structured_chat(
                prompts.CHECKER_SYSTEM,
                user_text + "\n\nExtracted screen text:\n" + ocr_text,
                CheckerOutput,
                stage="checker",
                payload_extra={"flag_id": flag.flag_id},
            )
        else:
            if not frame_paths:
                raise RuntimeError("no frames extracted for this flag's window")
            output = client.structured_chat(
                prompts.CHECKER_SYSTEM,
                user_text,
                CheckerOutput,
                images=frame_paths,
                stage="checker",
                payload_extra={"flag_id": flag.flag_id},
            )
        return _apply_resolution(doc, flag, output, frame_paths)
    except Exception as exc:  # noqa: BLE001 - one flag's failure must degrade, not fail the run
        client.metrics.record_failure("checker", str(exc), meta={"flag_id": flag.flag_id})
        flags = [
            f.model_copy(update={"status": "check_failed"}) if f.flag_id == flag.flag_id else f
            for f in doc.flags
        ]
        return doc.model_copy(update={"flags": flags})


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
    if that is unresolvable, level 1 widens to +/-SCREEN_ESCALATION_EVENTS events and (OpenAI only,
    Sarvam has no vision model) attaches the images. At most 2 calls per flag, and the model never
    decides how much context it gets. Same failure contract as _check_one_flag."""
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
            pack = build_evidence(events, timeline_dir, evidence_dir / flag.flag_id / f"level{level}", ocr)
            if not pack.after_frames:
                raise RuntimeError("no readable keyframes for this flag's events")
            use_images = level == 1 and config.LLM_PROVIDER != "sarvam" and bool(pack.images)
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
                payload_extra={"flag_id": flag.flag_id, "level": level, "tier": tier},
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
    video_path: Path,
    checker_frames_dir: Path,
    timeline=None,
    timeline_dir: Optional[Path] = None,
    ocr=None,
) -> SOPDocument:
    """Phase 3: resolve every open flag, severity-first, up to MAX_CHECKED_FLAGS. Frame extraction
    reuses core/io/video.py: extract_frames_window as-is -- it already handles cross-flag frame
    sharing (filename-based dedupe in one flat out_dir) and grid quantization, so overlapping
    windows cost nothing extra here. Sarvam batches every checked flag's frames into ONE
    document-intelligence job before the per-flag loop (see SarvamClient.ocr_extract_frames) --
    job-based OCR has real per-job latency, so one job per flag would be unacceptable; OpenAI skips
    this and sends real frames per call instead.
    """
    checked, unchecked = _select_flags_for_check(doc)
    if not checked and not unchecked:
        return doc

    checker_frames_dir.mkdir(parents=True, exist_ok=True)
    # v3: a flag with screen events attached is checked from event evidence (no frame extraction, no
    # OCR batch); every other flag takes the unchanged v2 path below.
    event_flag_ids = {f.flag_id for f in checked if timeline is not None and f.event_ids}
    frames_by_flag: Dict[str, List[Path]] = {}
    for flag in (f for f in checked if f.flag_id not in event_flag_ids):
        start_s, end_s = _flag_window_s(flag, doc, transcript)
        try:
            extracted = video_io.extract_frames_window(
                video_path,
                checker_frames_dir,
                start_s,
                end_s,
                interval_s=config.CHECKER_FRAME_INTERVAL_S,
                max_frames=config.CHECKER_MAX_FRAMES_PER_FLAG,
            )
            frames_by_flag[flag.flag_id] = [ef.path for ef in extracted]
        except Exception as exc:  # noqa: BLE001 - one flag's extraction failing shouldn't block the rest
            client.metrics.record_failure("checker_extract", str(exc), meta={"flag_id": flag.flag_id})
            frames_by_flag[flag.flag_id] = []

    ocr_text_by_frame: Dict[str, str] = {}
    if config.LLM_PROVIDER == "sarvam":
        seen: set[str] = set()
        all_frames: List[Path] = []
        for flag in (f for f in checked if f.flag_id not in event_flag_ids):
            for fp in frames_by_flag.get(flag.flag_id, []):
                if fp.name not in seen:
                    seen.add(fp.name)
                    all_frames.append(fp)
        batch = video_io.subsample_evenly(all_frames, config.CHECKER_MAX_OCR_FRAMES)
        if batch:
            try:
                ocr_text_by_frame = client.ocr_extract_frames(batch, workdir=checker_frames_dir)
            except Exception as exc:  # noqa: BLE001 - whole OCR batch failing shouldn't block extraction results
                client.metrics.record_failure("checker_ocr", str(exc), meta={"frame_count": len(batch)})

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
        if flag.flag_id in event_flag_ids:
            doc = _check_flag_with_events(
                client, doc, flag, transcript, timeline, timeline_dir, checker_frames_dir / "events", ocr
            )
        else:
            doc = _check_one_flag(client, doc, flag, transcript, frames_by_flag.get(flag.flag_id, []), ocr_text_by_frame)

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


def _reconcile_stage(
    client, run_dir: Path, doc: SOPDocument, timeline, segments_raw: list[dict], transcript: List[TranscriptSegment]
) -> Optional[SOPDocument]:
    """Stage 2c (docs/24): one batched call mapping steps onto screen events and saying what changed
    for every step. Returns None on any failure (recorded as `reconcile_failed`) so the caller falls
    back to Align's counting heuristic."""
    try:
        ocr = get_ocr()
        if ocr is None:
            raise RuntimeError("no local OCR engine available (pip install rapidocr-onnxruntime)")
        records = build_change_records(timeline, run_dir / "screen", ocr)
        storage.save_json([r.model_dump(mode="json") for r in records], run_dir / "change_records.json")
        output = client.structured_chat(
            prompts.RECONCILER_SYSTEM,
            build_reconciler_input(doc, records, segments_raw),
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
) -> RunArtifacts:
    """Phase 1+2: audio extraction -> STT -> Writer -> Reviewer -> sop_draft.md. Checker (Phase 3)
    is not implemented yet; frame_interval_s is accepted for API compatibility with the Streamlit
    UI and future phases but is currently only used for the sparse frame pass, which nothing
    downstream consumes yet.
    """
    validate_input(video_file)  # refuse before any file is copied or any call is made
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

        # Degraded-STT guard: parse_sarvam_stt_result falls back to a single segment spanning the
        # whole transcript (start=0.0, end=0.0) when per-chunk timing is unavailable. Every
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
        if use_screen and not timing_degraded:
            with metrics.stage("screen"):
                timeline = _screen_stage(client, run_dir, local_video)
            if timeline is not None:
                with metrics.stage("align"):
                    attached = _align_stage(client, run_dir, sop, timeline, transcript, silent_flags=not reconcile)
                reconciled = None
                if reconcile:
                    with metrics.stage("reconcile"):
                        reconciled = _reconcile_stage(client, run_dir, attached, timeline, segments_raw, transcript)
                    if reconciled is None:  # fall back to Align's counting heuristic for silent steps
                        attached = _align_stage(client, run_dir, sop, timeline, transcript, silent_flags=True)
                sop = reconciled if reconciled is not None else attached

        sop_draft_path = sop_render.write_sop(
            sop, run_dir, video_name=video_file.name, filename="sop_draft.md"
        )

        checker_frames_dir = run_dir / "checker_frames"
        with metrics.stage("checker"):
            sop = _run_checker(
                client, sop, transcript, local_video, checker_frames_dir,
                timeline=timeline, timeline_dir=run_dir / "screen", ocr=get_ocr() if timeline is not None else None,
            )

        sop_md_path = sop_render.write_sop(sop, run_dir, video_name=video_file.name, filename="sop.md")
        sop_render.write_sop_json(sop, run_dir)
        build_spec_path = write_build_spec(sop, run_dir)

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


