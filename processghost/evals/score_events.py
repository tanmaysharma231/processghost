"""Score a screen timeline against hand-labelled events. Pure Python, no API calls.

A detected event matches a label when their time ranges overlap after widening both by --tol
seconds. Matching is one-to-one, closest start times first. Reports precision (detected events
that match a label), recall (labels that were detected), the missed labels, the false events, and
how often the matched pair agreed on event type.

Usage (from the processghost/ directory):
    python -m evals.score_events --case bills_tracker --timeline runs/screen-main/screen_events.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

CASES_DIR = Path(__file__).parent / "cases"


def _overlaps(a0: float, a1: float, b0: float, b1: float, tol: float) -> bool:
    return a0 - tol <= b1 and b0 - tol <= a1


def score_events(detected: List[dict], labels: List[dict], tol: float = 0.5) -> Dict:
    """`detected` items need t_start/t_end/type/event_id; `labels` items need t_start/t_end/label_id
    and optionally type. Returns a dict of metrics plus matched/missed/false lists."""
    candidates: List[Tuple[float, int, int]] = []
    for i, d in enumerate(detected):
        for j, lab in enumerate(labels):
            if _overlaps(d["t_start"], d["t_end"], lab["t_start"], lab["t_end"], tol):
                candidates.append((abs(d["t_start"] - lab["t_start"]), i, j))
    candidates.sort()

    used_d, used_l = set(), set()
    matches = []
    for _dist, i, j in candidates:
        if i in used_d or j in used_l:
            continue
        used_d.add(i)
        used_l.add(j)
        matches.append((detected[i], labels[j]))

    typed = [(d, lab) for d, lab in matches if lab.get("type")]
    return {
        "n_detected": len(detected),
        "n_labels": len(labels),
        "precision": len(matches) / len(detected) if detected else 0.0,
        "recall": len(matches) / len(labels) if labels else 0.0,
        "type_agreement": (sum(1 for d, lab in typed if d["type"] == lab["type"]) / len(typed)) if typed else None,
        "matched": [{"event": d["event_id"], "label": lab["label_id"], "type_ok": d["type"] == lab.get("type", d["type"])} for d, lab in matches],
        "missed_labels": [lab["label_id"] for j, lab in enumerate(labels) if j not in used_l],
        "false_events": [d["event_id"] for i, d in enumerate(detected) if i not in used_d],
    }


def load_labels(case: str, include_unconfirmed: bool) -> List[dict]:
    data = json.loads((CASES_DIR / case / "expected_events.json").read_text(encoding="utf-8"))
    return [e for e in data["events"] if include_unconfirmed or e.get("confirmed")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", required=True)
    parser.add_argument("--timeline", required=True, type=Path, help="a screen_events.json")
    parser.add_argument("--tol", type=float, default=0.5)
    parser.add_argument("--confirmed-only", action="store_true", help="ignore labels not yet confirmed by a human")
    args = parser.parse_args()

    timeline = json.loads(args.timeline.read_text(encoding="utf-8"))
    labels = load_labels(args.case, include_unconfirmed=not args.confirmed_only)
    result = score_events(timeline["events"], labels, args.tol)

    print("labels used: %d (%s)" % (len(labels), "confirmed only" if args.confirmed_only else "including unconfirmed drafts"))
    print("detected %d | precision %.2f | recall %.2f | type agreement %s" % (
        result["n_detected"], result["precision"], result["recall"],
        "n/a" if result["type_agreement"] is None else "%.2f" % result["type_agreement"]))
    print("missed labels:", result["missed_labels"] or "none")
    print("false events: ", result["false_events"] or "none")


if __name__ == "__main__":
    main()
