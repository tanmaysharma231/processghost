"""Single-model multimodal baseline (design doc ADR-012): ONE well-prompted vision call over the whole
transcript and a fixed set of evenly spaced frames. No pipeline, no screen timeline, no per-flag calls.

It exists to answer "does the adaptive pipeline beat a strong simple baseline?" Give it the same evidence
(`--transcript-from` reuses a finished run's transcript so no second transcription is paid for or differs)
and say how many frames it got; the comparison is only fair when that budget is stated next to the result.

Its output is written as sop.md with one finding per line so evals/score_findings.py scores it unchanged.
Costs real money when run (one vision call). Not run as part of the test suite.

Usage (from the processghost/ directory, OPENAI_API_KEY set):
    python -m evals.baseline_single_model --video ../data/english_hackathon_test.mp4 --transcript-from runs/<id> --frames 12
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import cv2
from pydantic import BaseModel, Field

from core import config
from core.llm.client import Metrics
from core.models.sop import FindingKind
from core.models.tokens import TranscriptSegment
from core.pipeline import _make_client

BASELINE_SYSTEM = """You review a narrated screen-recording walkthrough of a business process. You are given the timestamped narration and a set of evenly spaced frames from the recording, each labelled with its time.

Find the gaps between what the speaker SAYS and what the screen SHOWS that someone would need answered before they could reproduce the process: actions on screen that were never explained, references like "here" or "that one" that were never resolved, rules or thresholds that were assumed, and claims that nothing on screen supports.

For each finding give its kind, one plain statement, the time it concerns, and a short verbatim quote from the narration or text visible on screen that it rests on. A claim the frames do not show is "unsupported_assertion", not disproved: the frames are a sample, not the whole recording. If you cannot tell, say so rather than guess."""


class BaselineFinding(BaseModel):
    kind: FindingKind
    statement: str = Field(min_length=1)
    at_s: Optional[float] = None
    evidence_quote: str = ""


class BaselineOutput(BaseModel):
    findings: List[BaselineFinding] = Field(default_factory=list)


def evenly_spaced_times(duration_s: float, n: int) -> List[float]:
    """Centres of `n` equal slices of the recording: no frame at the exact start or end."""
    if n < 1 or duration_s <= 0:
        return []
    return [round((i + 0.5) * duration_s / n, 2) for i in range(n)]


def grab_frames(video: Path, times: List[float], out_dir: Path, width: int = config.SCREEN_EVIDENCE_FULL_WIDTH) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    paths: List[Path] = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ok, frame = cap.read()
        if not ok:
            continue
        if frame.shape[1] > width:
            frame = cv2.resize(frame, (width, int(frame.shape[0] * width / frame.shape[1])), interpolation=cv2.INTER_AREA)
        path = out_dir / f"t{t:07.2f}.png"
        cv2.imwrite(str(path), frame)
        paths.append(path)
    cap.release()
    return paths


def render_baseline_md(out: BaselineOutput, *, frames: int) -> str:
    lines = ["# Single-model baseline", "", f"Evidence: the full transcript and {frames} evenly spaced frames, one model call.", "", "## Findings", ""]
    for f in out.findings:
        at = f" (@{f.at_s:.0f}s)" if f.at_s is not None else ""
        quote = f' -- "{f.evidence_quote.strip()}"' if f.evidence_quote.strip() else ""
        lines.append(f"- [{f.kind}]{at} {' '.join(f.statement.split())}{quote}")
    return "\n".join(lines) + "\n"


def load_transcript(run_dir: Path) -> List[TranscriptSegment]:
    raw = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["transcript"]
    return [TranscriptSegment.model_validate(s) for s in raw]


def video_duration_s(video: Path) -> float:
    cap = cv2.VideoCapture(str(video))
    try:
        return (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 30.0)
    finally:
        cap.release()


def run_single_model(client, video: Path, transcript: List[TranscriptSegment], n_frames: int, out_dir: Path) -> BaselineOutput:
    times = evenly_spaced_times(video_duration_s(video), n_frames)
    frames = grab_frames(video, times, out_dir / "frames")
    narration = "\n".join(f"[{s.start_s:.1f}-{s.end_s:.1f}s] {s.text.strip()}" for s in transcript)
    labels = "\n".join(f"image {i + 1}: frame at t={t:.2f}s" for i, t in enumerate(times[: len(frames)]))
    output = client.structured_chat(
        BASELINE_SYSTEM,
        f"Narration:\n{narration}\n\nFrames, in order:\n{labels}",
        BaselineOutput,
        images=frames,
        stage="baseline",
    )
    (out_dir / "sop.md").write_text(render_baseline_md(output, frames=len(frames)), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--transcript-from", required=True, type=Path, help="a finished run dir whose run.json supplies the transcript")
    parser.add_argument("--frames", type=int, default=12, help="how many evenly spaced frames the model sees")
    parser.add_argument("--provider", default="openai", choices=["openai"])
    args = parser.parse_args()

    config.LLM_PROVIDER = args.provider
    out_dir = config.RUNS_DIR / ("baseline-" + datetime.utcnow().strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = Metrics()
    metrics.trace_path = out_dir / "trace.jsonl"
    client = _make_client(metrics)
    result = run_single_model(client, args.video, load_transcript(args.transcript_from), args.frames, out_dir)
    print("findings: %d | frames: %d | tokens in/out: %d/%d | cost: $%.4f" % (len(result.findings), args.frames, metrics.tokens_in, metrics.tokens_out, metrics.cost_usd))
    print("wrote", out_dir)


if __name__ == "__main__":
    main()
