"""Score pipeline output against the required findings in docs/12 (see expected_findings.json).

A finding counts as surfaced when any of its pattern groups matches inside a single line of sop.md.
This is a deliberately crude check: it shows whether the right facts appear anywhere in the output,
not that they appear as correct, well-placed answers -- read the matching lines it prints. It also
reports forbidden claims (statements already seen to be false in real runs) and whether the output
claims completeness ("Nothing is missing") while findings are still missing.

Score existing runs:
    python -m evals.score_findings runs/20261011-074659-2dbb1b runs/20261011-075004-5d33c7

Run the pipeline N times with the cache off, then score (costs real money):
    python -m evals.score_findings --run 3 --video ../data/english_hackathon_test.mp4 --screen
    python -m evals.score_findings --run 3 --video ../data/english_hackathon_test.mp4 --screen --no-reconcile
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List

CASES_DIR = Path(__file__).parent / "cases"


def load_spec(case: str = "bills_tracker") -> dict:
    return json.loads((CASES_DIR / case / "expected_findings.json").read_text(encoding="utf-8"))


def score_text(sop_md: str, spec: dict) -> Dict:
    """Pure scoring of one sop.md against the spec."""
    lines = sop_md.splitlines()
    results = []
    for f in spec["findings"]:
        hit_line = None
        exclude = f.get("exclude", [])
        for group in f["groups"]:
            for line in lines:
                if any(re.search(x, line, re.I) for x in exclude):
                    continue  # a line that matches an exclude pattern never counts for this finding
                if all(re.search(p, line, re.I) for p in group):
                    hit_line = line.strip()
                    break
            if hit_line:
                break
        results.append({"id": f["id"], "name": f["name"], "surfaced": hit_line is not None, "line": hit_line})
    forbidden = [x["pattern"] for x in spec.get("forbidden", []) if re.search(x["pattern"], sop_md, re.I)]
    banner = any(re.search(p, sop_md, re.I) for p in spec.get("completeness_banners", []))
    n_found = sum(r["surfaced"] for r in results)
    return {
        "findings": results,
        "n_surfaced": n_found,
        "n_required": len(results),
        "forbidden_claims": forbidden,
        "false_completeness": banner and n_found < len(results),
    }


def aggregate(scores: List[Dict]) -> Dict[str, float]:
    """Per-finding hit rate across runs."""
    if not scores:
        return {}
    ids = [r["id"] for r in scores[0]["findings"]]
    return {i: sum(s["findings"][k]["surfaced"] for s in scores) / len(scores) for k, i in enumerate(ids)}


def _print(label: str, score: Dict, verbose: bool) -> None:
    flags = "".join("Y" if r["surfaced"] else "." for r in score["findings"])
    print("%-28s %d/%d  [%s]  forbidden=%d  false_completeness=%s" % (
        label, score["n_surfaced"], score["n_required"], flags, len(score["forbidden_claims"]), score["false_completeness"]))
    if verbose:
        for r in score["findings"]:
            if r["surfaced"]:
                print("    %s: %s" % (r["id"], (r["line"] or "")[:150].encode("ascii", "replace").decode()))
        for p in score["forbidden_claims"]:
            print("    FORBIDDEN CLAIM:", p)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dirs", nargs="*", type=Path, help="existing run directories (each with sop.md)")
    parser.add_argument("--run", type=int, default=0, help="run the pipeline this many times first (no cache)")
    parser.add_argument("--video", type=Path, help="the recording to run when --run is given")
    parser.add_argument("--screen", action="store_true")
    parser.add_argument("--no-reconcile", action="store_true")
    parser.add_argument("--case", default="bills_tracker")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.run and not args.video:
        parser.error("--run needs --video")
    spec = load_spec(args.case)
    run_dirs = list(args.run_dirs)
    cost = 0.0
    if args.run:
        from core import config
        from core.pipeline import run_pipeline

        config.CACHE_ENABLED = False  # a cached rerun would replay the first answer and hide variance
        config.LLM_PROVIDER = "openai"
        for _ in range(args.run):
            art = run_pipeline(args.video, use_screen=args.screen, reconcile=not args.no_reconcile)
            cost += art.metrics["cost_usd_estimate"]
            run_dirs.append(art.sop_md.parent)

    scores = []
    for d in run_dirs:
        s = score_text((d / "sop.md").read_text(encoding="utf-8"), spec)
        scores.append(s)
        _print(d.name, s, args.verbose)

    if scores:
        print("-" * 70)
        rates = aggregate(scores)
        print("hit rate over %d run(s): %s" % (len(scores), "  ".join("%s %.0f%%" % (k, v * 100) for k, v in rates.items())))
        print("runs with a forbidden claim: %d | runs with false completeness: %d" % (
            sum(1 for s in scores if s["forbidden_claims"]), sum(1 for s in scores if s["false_completeness"])))
        if cost:
            print("pipeline cost for the new runs: $%.3f" % cost)


if __name__ == "__main__":
    main()
