"""Score event classes (core/screen/classify.py) against hand-labelled expectations. Pure Python, no API.

The rule that matters is not overall accuracy but safety: a label whose true class is data_change must never
be classified into a class that is dropped from the flow (transient, view_only). That count is reported on
its own and should be zero. Records are matched to labels by time overlap (evals/score_events.match_by_time).

Usage (from the processghost/ directory):
    python -m evals.score_classes --case bills_tracker --records runs/<id>/change_records.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

from core.screen.classify import DROPPED_CLASSES
from evals.score_events import CASES_DIR, match_by_time


def score_classes(records: List[dict], labels: List[dict], tol: float = 0.5) -> Dict:
    pairs, _used_r, used_l = match_by_time(records, labels, tol)
    hits, wrong, unsafe = 0, [], []
    for rec, lab in pairs:
        actual = rec.get("event_class")
        if actual == lab["class"]:
            hits += 1
        else:
            wrong.append({"label": lab["label_id"], "event": rec["event_id"], "expected": lab["class"], "actual": actual})
        if lab["class"] == "data_change" and actual in DROPPED_CLASSES:
            unsafe.append(lab["label_id"])
    return {
        "n_records": len(records),
        "n_labels": len(labels),
        "matched": len(pairs),
        "correct": hits,
        "wrong": wrong,
        "data_changes_dropped": unsafe,
        "unmatched_labels": [lab["label_id"] for j, lab in enumerate(labels) if j not in used_l],
    }


def load_labels(case: str, include_unconfirmed: bool = True) -> List[dict]:
    data = json.loads((CASES_DIR / case / "expected_classes.json").read_text(encoding="utf-8"))
    return [e for e in data["events"] if include_unconfirmed or e.get("confirmed")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", required=True)
    parser.add_argument("--records", required=True, type=Path, help="a change_records.json")
    parser.add_argument("--tol", type=float, default=0.5)
    parser.add_argument("--confirmed-only", action="store_true")
    args = parser.parse_args()

    records = json.loads(args.records.read_text(encoding="utf-8"))
    r = score_classes(records, load_labels(args.case, not args.confirmed_only), args.tol)
    print("records %d, labels %d, matched %d, correct class %d" % (r["n_records"], r["n_labels"], r["matched"], r["correct"]))
    print("data changes classified as dropped: %d %s" % (len(r["data_changes_dropped"]), r["data_changes_dropped"] or ""))
    for m in r["wrong"]:
        print("  MISS %s %s: expected %s, got %s" % (m["label"], m["event"], m["expected"], m["actual"]))
    if r["unmatched_labels"]:
        print("  labels with no matching event:", ", ".join(r["unmatched_labels"]))


if __name__ == "__main__":
    main()
