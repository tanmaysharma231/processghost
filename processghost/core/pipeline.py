"""End-to-end pipeline orchestrator.

Intent extraction and action description are backed by real OpenAI chat/vision calls (see
core/llm/prompts.py and core/llm/parsing.py). Each has a heuristic fallback used only when the live
call fails or returns something that doesn't parse -- every fallback used is recorded on the run's
metrics (Metrics.record_failure) rather than happening silently, so a run can always be audited for
where it degraded to the heuristic path. See Process_ghost/docs/08-target-pipeline-spec.md for the
per-item failure-handling rationale this follows.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import List

from core import config
from core.align.aligner import build_alignments
from core.io import storage, video
from core.io.images import frame_to_data_uri
from core.llm.client import LLMClient, Metrics, persist_metrics, persist_run_json
from core.llm import prompts
from core.llm.parsing import extract_chat_content, parse_action_response, parse_intent_response
from core.models.tokens import ActionToken, IntentToken, RunArtifacts, TranscriptSegment
from core.rules import engine
from core.rules import r_contradiction, r_silent_step, r_teleportation, r_unexplained, r_vague_intent
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


def _intent_from_segment(client: LLMClient, seg: dict) -> IntentToken:
    """Extract the intent from one transcript segment via a real chat call."""
    text = seg.get("text", "")
    if not text.strip():
        return _intent_heuristic(seg)
    try:
        response = client.chat(
            [
                {"role": "system", "content": prompts.INTENT_EXTRACTION_SYSTEM},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
        )
        content = extract_chat_content(response)
        return parse_intent_response(content, seg=seg)
    except Exception as exc:  # noqa: BLE001 - any failure here must degrade, not fail the run
        client.metrics.record_failure(
            "intent_extraction", str(exc), meta={"segment_start": seg.get("start")}
        )
        return _intent_heuristic(seg)


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

    intents: List[IntentToken] = [_intent_from_segment(client, seg) for seg in segments_raw]

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

    artifacts = RunArtifacts(
        run_id=run_dir.name,
        video_path=local_video,
        audio_path=audio_path,
        frames_dir=frames_dir,
        transcript=transcript,
        intents=intents,
        actions=actions,
        alignments=alignments,
    )

    rules = [r_unexplained.rule, r_silent_step.rule, r_vague_intent.rule, r_teleportation.rule]
    if config.ENABLE_CONTRADICTION:
        rules.append(r_contradiction.rule)

    artifacts = engine.run_rules(artifacts, rules)
    artifacts = render.write_reports(artifacts, run_dir)

    duration_s = time.time() - t0
    artifacts.metrics = metrics.to_dict() | {"duration_s": duration_s}
    persist_metrics(artifacts.run_id, metrics, video_name=video_file.name, duration_s=duration_s)
    persist_run_json(run_dir, artifacts.model_dump())
    return artifacts
