"""Run the full pipeline on a video and print a summary. Costs real API money.

Usage (from the processghost/ directory, OPENAI_API_KEY set, ffmpeg on PATH):
    python -m scripts.run_pipeline ../data/english_hackathon_test.mp4            # v2 behaviour
    python -m scripts.run_pipeline ../data/english_hackathon_test.mp4 --screen   # v3: screen timeline + Align + event Checker
"""
from __future__ import annotations

import argparse
from pathlib import Path

from core import config
from core.pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("video", type=Path)
    parser.add_argument("--screen", action="store_true", help="enable the v3 screen timeline, Align and event-evidence Checker")
    parser.add_argument("--no-reconcile", action="store_true", help="with --screen: skip the Reconciler and use Align's counting heuristic (for comparison)")
    parser.add_argument("--workbook", type=Path, default=None, help="the workbook the person worked in: enables the read-only document rung (questions the video left open are checked against it)")
    parser.add_argument("--provider", default="openai", choices=["openai", "anthropic"])
    parser.add_argument("--no-cache", action="store_true", help="ignore and do not write the model-call cache")
    parser.add_argument("--no-keep-media", action="store_true", help="delete the run's video copy, audio and frames when it ends")
    parser.add_argument("--max-cost", type=float, default=None, help="soft per-run budget in USD (default: config.MAX_RUN_COST_USD)")
    args = parser.parse_args()

    config.LLM_PROVIDER = args.provider
    config.CACHE_ENABLED = not args.no_cache
    config.KEEP_RUN_MEDIA = not args.no_keep_media
    if args.max_cost is not None:
        config.MAX_RUN_COST_USD = args.max_cost
    art = run_pipeline(args.video, use_screen=args.screen, reconcile=not args.no_reconcile, workbook=args.workbook)

    doc = art.sop
    print("run:", art.run_id)
    print("steps: %d | flags: %d | review_failed: %s" % (len(doc.steps), len(doc.flags), doc.review_failed))
    for f in doc.flags:
        print(
            "  %-8s %-10s %-9s %-12s origin=%-8s tier=%-8s level=%s events=%s"
            % (f.flag_id, f.family, f.severity, f.status, f.origin, f.checker_tier, f.expansion_level, f.event_ids)
        )
    m = art.metrics
    print("tokens in/out: %s/%s | cost: $%.4f | failures: %d" % (m["tokens_in"], m["tokens_out"], m["cost_usd_estimate"], len(m["fallbacks"])))
    for fb in m["fallbacks"]:
        print("  failure:", fb["stage"], str(fb["error"])[:120])
    print("outputs under", art.sop_md.parent)


if __name__ == "__main__":
    main()
