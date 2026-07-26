"""End-to-end pipeline orchestrator.

Intent extraction, action description, and cross-checking are backed by real OpenAI chat/vision
calls (see core/llm/prompts.py and core/llm/parsing.py). Each has a fallback used only when the
live call fails or returns something that doesn't parse -- every fallback used is recorded on the
run's metrics (Metrics.record_failure) rather than happening silently, so a run can always be
audited for where it degraded. See Process_ghost/docs/08-target-pipeline-spec.md for the
failure-handling rationale this follows.

Intent extraction and cross-checking are each ONE call per run (not one per transcript segment or
one per intent-action pair) -- giving the model the whole transcript, or the whole set of extracted
entities/claims plus a compact summary of observed actions, at once lets it do cross-referencing
across the entire recording that a narrower per-fragment call structurally cannot do, at lower
total cost than many small calls. See Process_ghost's plan history for the full reasoning.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import List, Tuple

from core import config
from core.align.aligner import build_alignments
from core.io import storage, video
from core.io.images import frame_to_data_uri
from core.llm.client import LLMClient, Metrics, persist_metrics, persist_run_json
from core.llm import prompts
from core.llm.parsing import (
    extract_chat_content,
    parse_action_response,
    parse_cross_check_response,
    parse_transcript_extraction_response,
)
from core.models.tokens import (
    ActionToken,
    IntentToken,
    RunArtifacts,
    TranscriptSegment,
    UnconfirmedClaimFinding,
    UnresolvedEntityFinding,
)
from core.rules import engine
from core.rules import (
    r_contradiction,
    r_silent_step,
    r_teleportation,
    r_unconfirmed_outcome,
    r_unexplained,
    r_unresolved_reference,
)
from report import render


def _intent_heuristic(seg: dict) -> IntentToken:
    """Keyword-based fallback used when the LLM call fails or its response doesn't parse."""
    text = seg.get("text", "")
    is_vague = any(k in text.lower() for k in ["check", "verify", "review", "make sure"])
    return IntentToken(
        intent_id=f"intent_{int(seg.get('id', seg['start']))}",
        t_s=float(seg["start"]),
        span_s=float(seg["end"] - seg["start"]),
        raw_text=text,
        intent=text,
        verb=None,
        obj=None,
        confidence=seg.get("confidence", 0.6),
        is_vague=is_vague,
    )


def _extract_intents_from_transcript(client: LLMClient, segments_raw: list[dict]) -> List[IntentToken]:
    """Extract intents (and entities/selection-criteria/claimed-outcomes) for the WHOLE transcript
    in a single chat call. On any failure -- the call itself, a wrong-length array, a field that
    doesn't validate -- falls back to the keyword heuristic for every segment in the batch, and
    records exactly one failure on the run's metrics."""
    if not segments_raw:
        return []
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
        response = client.chat(
            [
                {"role": "system", "content": prompts.TRANSCRIPT_EXTRACTION_SYSTEM},
                {"role": "user", "content": str(numbered)},
            ],
            response_format={"type": "json_object"},
        )
        content = extract_chat_content(response)
        return parse_transcript_extraction_response(content, segments_raw)
    except Exception as exc:  # noqa: BLE001 - any failure here must degrade, not fail the run
        client.metrics.record_failure(
            "transcript_extraction", str(exc), meta={"segment_count": len(segments_raw)}
        )
        return [_intent_heuristic(seg) for seg in segments_raw]


def _action_heuristic(idx: int, frame_path: Path, t_s: float) -> ActionToken:
    """Stub fallback used when the vision call fails, its response doesn't parse, or a frame was
    subsampled out of the per-run vision-call budget (see config.MAX_VISION_CALLS_PER_RUN)."""
    return ActionToken(
        action_id=f"action_{idx:04d}",
        t_s=t_s,
        frame_path=frame_path,
        action_type="unknown",
        target=None,
        details="",
        app=None,
        confidence=0.3,
        roi=None,
    )


def _action_from_frame(client: LLMClient, idx: int, frame_path: Path, t_s: float) -> ActionToken:
    """Describe one extracted frame via a real vision call."""
    try:
        data_uri = frame_to_data_uri(frame_path, max_dimension=config.VISION_MAX_DIMENSION)
        response = client.vision(
            [
                {"role": "system", "content": prompts.ACTION_DESCRIPTION_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this frame."},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            response_format={"type": "json_object"},
        )
        content = extract_chat_content(response)
        return parse_action_response(content, idx=idx, frame_path=frame_path, t_s=t_s)
    except Exception as exc:  # noqa: BLE001 - any failure here must degrade, not fail the run
        client.metrics.record_failure(
            "action_description", str(exc), meta={"frame": str(frame_path)}
        )
        return _action_heuristic(idx, frame_path, t_s)


def _cross_check_resolution(
    client: LLMClient, intents: List[IntentToken], actions: List[ActionToken]
) -> Tuple[List[UnresolvedEntityFinding], List[UnconfirmedClaimFinding]]:
    """One chat call, after both intents and actions are known: given compact text summaries of
    what was said (entities referenced, selection criteria, claimed outcomes) and what was
    observed (action type/target/details per sampled frame), find which entities are still
    unresolved and which claims are still unconfirmed across the WHOLE run. On any failure,
    records one failure on the run's metrics and returns two empty lists -- the two rules that
    consume this simply produce no findings for the run rather than crashing it."""
    narration_items = [
        {
            "intent_id": i.intent_id,
            "entities_referenced": [e.model_dump() for e in i.entities_referenced],
            "selection_criteria": i.selection_criteria,
            "claimed_outcome": i.claimed_outcome,
        }
        for i in intents
        if i.entities_referenced or i.selection_criteria or i.claimed_outcome
    ]
    observed_actions = [
        {
            "action_id": a.action_id,
            "t_s": a.t_s,
            "action_type": a.action_type,
            "target": a.target,
            "details": a.details,
        }
        for a in actions
    ]

    if not narration_items:
        return [], []

    try:
        response = client.chat(
            [
                {"role": "system", "content": prompts.CROSS_CHECK_SYSTEM},
                {
                    "role": "user",
                    "content": str({"narration_items": narration_items, "observed_actions": observed_actions}),
                },
            ],
            response_format={"type": "json_object"},
        )
        content = extract_chat_content(response)
        return parse_cross_check_response(content)
    except Exception as exc:  # noqa: BLE001 - any failure here must degrade, not fail the run
        client.metrics.record_failure(
            "cross_check", str(exc), meta={"intent_count": len(intents), "action_count": len(actions)}
        )
        return [], []


def run_pipeline(video_file: Path, frame_interval_s: float = config.FRAME_INTERVAL_S) -> RunArtifacts:
    t0 = time.time()
    metrics = Metrics()
    client = LLMClient(metrics=metrics)

    run_dir = storage.new_run_dir()
    local_video = run_dir / "video.mp4"
    shutil.copy(video_file, local_video)

    audio_path = run_dir / "audio.wav"
    video.extract_audio(local_video, audio_path)

    # Transcription via Whisper
    transcript_resp = client.transcribe(audio_path)
    segments_raw = transcript_resp.get("segments", [])
    transcript = [
        TranscriptSegment(
            start_s=float(seg["start"]),
            end_s=float(seg["end"]),
            text=seg.get("text", ""),
            speaker=None,
        )
        for seg in segments_raw
    ]

    # One call for the whole transcript (not one per segment) -- see module docstring.
    intents: List[IntentToken] = _extract_intents_from_transcript(client, segments_raw)

    # Frame sampling: extract at a fixed interval, then cap how many of those frames actually get
    # a vision call (cost/latency guard) by evenly subsampling. Frames outside the vision budget
    # still get an ActionToken, just via the heuristic stub rather than a real description -- see
    # Process_ghost/docs/09-visual-gap-filler.md for the fuller scene-change-sampling design this
    # is a placeholder for.
    frames_dir = run_dir / "frames"
    frames = video.extract_frames(local_video, frames_dir, interval_s=frame_interval_s)
    frames_for_vision = set(video.subsample_evenly(frames, config.MAX_VISION_CALLS_PER_RUN))

    # naive timestamp mapping: interval * idx
    actions: List[ActionToken] = []
    for idx, fp in enumerate(frames):
        t_s = idx * frame_interval_s
        if fp in frames_for_vision:
            actions.append(_action_from_frame(client, idx, fp, t_s))
        else:
            actions.append(_action_heuristic(idx, fp, t_s))

    alignments = build_alignments(actions, intents)

    # One call, after both intents and actions are known -- see module docstring.
    unresolved_entities, unconfirmed_claims = _cross_check_resolution(client, intents, actions)

    artifacts = RunArtifacts(
        run_id=run_dir.name,
        video_path=local_video,
        audio_path=audio_path,
        frames_dir=frames_dir,
        transcript=transcript,
        intents=intents,
        actions=actions,
        alignments=alignments,
        unresolved_entities=unresolved_entities,
        unconfirmed_claims=unconfirmed_claims,
    )

    rules = [
        r_unexplained.rule,
        r_silent_step.rule,
        r_unresolved_reference.rule,
        r_unconfirmed_outcome.rule,
        r_teleportation.rule,
    ]
    if config.ENABLE_CONTRADICTION:
        rules.append(r_contradiction.rule)

    artifacts = engine.run_rules(artifacts, rules)
    artifacts = render.write_reports(artifacts, run_dir)

    duration_s = time.time() - t0
    artifacts.metrics = metrics.to_dict() | {"duration_s": duration_s}
    persist_metrics(artifacts.run_id, metrics, video_name=video_file.name, duration_s=duration_s)
    persist_run_json(run_dir, artifacts.model_dump())
    return artifacts
