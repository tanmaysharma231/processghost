"""Compare methods on one recording (design doc ADR-012): the v2 fixed-frame pipeline, the adaptive
pipeline (--screen), and the single-model multimodal baseline.

For each method it reports what was MEASURED: model calls and tokens per stage from the run's trace, the
estimated cost, how many of the required findings (expected_findings.json) the output surfaced, forbidden
claims, and false completeness. Nothing here asserts that one method is cheaper or more accurate: with one
clip and a few runs the numbers are a proof of concept, and the output says so.

Running it costs real money (every method, every repeat) and is not part of the test suite:
    python -m evals.compare_methods --video ../data/english_hackathon_test.mp4 --transcript-from runs/<id> --repeat 3
Table-only, from finished run dirs (free):
    python -m evals.compare_methods --score v2=runs/a,runs/b adaptive=runs/c single=runs/baseline-d
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from core import config
from core.llm.client import Metrics
from core.pipeline import _make_client, run_pipeline
from evals.baseline_single_model import load_transcript, run_single_model
from evals.score_findings import load_spec, score_text


def summarize_trace(run_dir: Path) -> Dict:
    """Calls, tokens and cost per stage, read from a run's trace.jsonl. Cache hits are real lines with
    zero cost and zero tokens, so a cached rerun shows its calls but not a charge."""
    by_stage: Dict[str, Dict[str, float]] = defaultdict(lambda: {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0})
    path = run_dir / "trace.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("event") != "call":
                continue
            usage = entry.get("usage") or {}
            s = by_stage[entry.get("stage", "unknown")]
            s["calls"] += 1
            s["tokens_in"] += usage.get("prompt_tokens", 0)
            s["tokens_out"] += usage.get("completion_tokens", 0)
            s["cost_usd"] += entry.get("cost_usd") or 0.0
    return {
        "by_stage": dict(by_stage),
        "calls": int(sum(s["calls"] for s in by_stage.values())),
        "tokens_in": int(sum(s["tokens_in"] for s in by_stage.values())),
        "cost_usd": sum(s["cost_usd"] for s in by_stage.values()),
    }


def method_row(name: str, run_dirs: List[Path], spec: dict) -> Dict:
    scores = [score_text((d / "sop.md").read_text(encoding="utf-8"), spec) for d in run_dirs]
    traces = [summarize_trace(d) for d in run_dirs]
    n = max(len(run_dirs), 1)
    return {
        "method": name,
        "runs": len(run_dirs),
        "surfaced_mean": sum(s["n_surfaced"] for s in scores) / n,
        "required": scores[0]["n_required"] if scores else 0,
        "forbidden_runs": sum(1 for s in scores if s["forbidden_claims"]),
        "false_completeness_runs": sum(1 for s in scores if s["false_completeness"]),
        "calls_mean": sum(t["calls"] for t in traces) / n,
        "tokens_in_mean": sum(t["tokens_in"] for t in traces) / n,
        "cost_mean": sum(t["cost_usd"] for t in traces) / n,
    }


def format_table(rows: List[Dict]) -> str:
    head = "%-10s %4s  %-12s %9s %9s %11s %9s %10s" % ("method", "runs", "surfaced", "forbidden", "false-cpl", "calls/run", "tok-in", "$ / run")
    lines = [head, "-" * len(head)]
    for r in rows:
        per_dollar = ("%.1f" % (r["surfaced_mean"] / r["cost_mean"])) if r["cost_mean"] > 0 else "n/a"
        lines.append(
            "%-10s %4d  %4.1f of %-5d %9d %9d %11.1f %9.0f %10.4f   findings per $: %s"
            % (r["method"], r["runs"], r["surfaced_mean"], r["required"], r["forbidden_runs"], r["false_completeness_runs"],
               r["calls_mean"], r["tokens_in_mean"], r["cost_mean"], per_dollar)
        )
    lines.append("")
    lines.append("POC only: one clip, few runs, a crude keyword scorer. These numbers do not show which method is better.")
    return "\n".join(lines)


def _run_all(args) -> Dict[str, List[Path]]:
    config.LLM_PROVIDER = "openai"
    config.CACHE_ENABLED = False  # a cached rerun would replay the first answer and hide variance
    runs: Dict[str, List[Path]] = {"v2": [], "adaptive": [], "single": []}
    transcript = load_transcript(args.transcript_from)
    for _ in range(args.repeat):
        runs["v2"].append(run_pipeline(args.video, use_screen=False).sop_md.parent)
        runs["adaptive"].append(run_pipeline(args.video, use_screen=True).sop_md.parent)
        out_dir = config.RUNS_DIR / f"baseline-{len(runs['single']) + 1}-{args.video.stem}"
        out_dir.mkdir(parents=True, exist_ok=True)
        metrics = Metrics()
        metrics.trace_path = out_dir / "trace.jsonl"
        run_single_model(_make_client(metrics), args.video, transcript, args.frames, out_dir)
        runs["single"].append(out_dir)
    return runs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video", type=Path)
    parser.add_argument("--transcript-from", type=Path, help="a finished run dir; the baseline reuses its transcript")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--frames", type=int, default=12, help="evenly spaced frames the single-model baseline sees")
    parser.add_argument("--score", nargs="*", help="name=dir[,dir...] pairs to tabulate without running anything")
    parser.add_argument("--case", default="bills_tracker")
    args = parser.parse_args()

    spec = load_spec(args.case)
    if args.score:
        runs = {a.split("=", 1)[0]: [Path(p) for p in a.split("=", 1)[1].split(",")] for a in args.score}
    else:
        if not (args.video and args.transcript_from):
            parser.error("running the methods needs --video and --transcript-from")
        runs = _run_all(args)
    print(format_table([method_row(name, dirs, spec) for name, dirs in runs.items()]))


if __name__ == "__main__":
    main()
