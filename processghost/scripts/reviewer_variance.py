"""Measure Reviewer run-to-run variance with the Writer draft held fixed.

Runs audio extraction + STT + Writer ONCE, then calls the Reviewer N times on that identical draft
and reports, per run: grounded transitions, structural vs ambiguity flags, and whether the review
failed. Because the input to every Reviewer call is byte-identical, any spread is model noise (see
docs/18-known-gaps-v2.md entry 1). The Checker is never run, so this costs roughly $0.01 per extra
run.

Usage (from the processghost/ directory, OPENAI_API_KEY set, ffmpeg on PATH):
    python -m scripts.reviewer_variance ../data/english_hackathon_test.mp4 --runs 10
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

from core import config
from core.io import storage
from core.io import video as video_io
from core.llm.client import Metrics
from core.pipeline import _make_client, _review_sop, _transcribe, _write_sop


def _summarize(doc) -> dict:
    structural = [f for f in doc.flags if f.family == "structural"]
    ambiguity = [f for f in doc.flags if f.family == "ambiguity"]
    return {
        "review_failed": doc.review_failed,
        "steps": len(doc.steps),
        "grounded_transitions": len(doc.transitions),
        "structural_flags": len(structural),
        "ambiguity_flags": len(ambiguity),
        "total_flags": len(doc.flags),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("video", type=Path)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--provider", default="openai", choices=["openai", "anthropic"])
    args = parser.parse_args()

    config.LLM_PROVIDER = args.provider
    metrics = Metrics()
    client = _make_client(metrics)

    work_dir = config.RUNS_DIR / ("variance-" + datetime.utcnow().strftime("%Y%m%d-%H%M%S"))
    work_dir.mkdir(parents=True, exist_ok=True)
    metrics.trace_path = work_dir / "trace.jsonl"
    audio_path = work_dir / "audio.wav"

    video_io.extract_audio(args.video, audio_path)
    segments_raw = _transcribe(client, audio_path)
    draft = _write_sop(client, work_dir.name, segments_raw)
    print("Writer draft: %d steps, %d consecutive pairs" % (len(draft.steps), max(len(draft.steps) - 1, 0)))

    results = []
    for i in range(args.runs):
        reviewed = _review_sop(client, draft, segments_raw)
        row = _summarize(reviewed)
        results.append(row)
        print(
            "run %2d: transitions=%d structural=%d ambiguity=%d total=%d%s"
            % (
                i + 1,
                row["grounded_transitions"],
                row["structural_flags"],
                row["ambiguity_flags"],
                row["total_flags"],
                "  REVIEW_FAILED" if row["review_failed"] else "",
            )
        )

    print("=" * 70)
    for key in ("grounded_transitions", "structural_flags", "ambiguity_flags", "total_flags"):
        vals = [r[key] for r in results]
        print(
            "%-22s min=%d median=%s max=%d"
            % (key, min(vals), statistics.median(vals), max(vals))
        )
    zero = sum(1 for r in results if r["grounded_transitions"] == 0)
    print("runs with zero grounded transitions: %d / %d" % (zero, len(results)))
    print("estimated cost so far: $%.4f" % metrics.cost_usd)

    storage.save_json(
        {"video": str(args.video), "provider": args.provider, "results": results, "cost_usd": metrics.cost_usd},
        work_dir / "variance.json",
    )
    print("saved: %s" % (work_dir / "variance.json"))


if __name__ == "__main__":
    main()
