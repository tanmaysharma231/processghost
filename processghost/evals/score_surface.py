"""Score surface locators (core/screen/surfaces) against hand-labelled expectations. Pure Python, no API.

Records are matched to labels by time overlap, one-to-one, closest start first (same rule as
evals/score_events.py). Only the fields a label lists under `expect` are scored, per field, so a label can
say "the sheet was switched to #2" without claiming anything about a cell reference nobody can read.

Usage (from the processghost/ directory):
    python -m evals.score_surface --case bills_tracker --records runs/<id>/change_records.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

from core.text_grounding import compact
from evals.score_events import CASES_DIR, _overlaps

FIELDS = ("change", "index", "location", "focus")


def _actual(record: dict, field: str):
    after = record.get("surface_after") or {}
    if field == "change":
        return record.get("surface_change")
    return after.get(field)


def _same(field: str, expected, actual) -> bool:
    if field in ("location", "focus"):
        return compact(str(expected)) == compact(str(actual or ""))
    return expected == actual


def score_surface(records: List[dict], labels: List[dict], tol: float = 0.5) -> Dict:
    candidates: List[Tuple[float, int, int]] = []
    for i, r in enumerate(records):
        for j, lab in enumerate(labels):
            if _overlaps(r["t_start"], r["t_end"], lab["t_start"], lab["t_end"], tol):
                candidates.append((abs(r["t_start"] - lab["t_start"]), i, j))
    candidates.sort()

    used_r, used_l, pairs = set(), set(), []
    for _d, i, j in candidates:
        if i in used_r or j in used_l:
            continue
        used_r.add(i)
        used_l.add(j)
        pairs.append((records[i], labels[j]))

    per_field = {f: [0, 0] for f in FIELDS}  # [hits, scored]
    mismatches = []
    for rec, lab in pairs:
        for field, expected in lab["expect"].items():
            actual = _actual(rec, field)
            per_field[field][1] += 1
            if _same(field, expected, actual):
                per_field[field][0] += 1
            else:
                mismatches.append({"label": lab["label_id"], "event": rec["event_id"], "field": field, "expected": expected, "actual": actual})
    return {
        "n_records": len(records),
        "n_labels": len(labels),
        "matched": len(pairs),
        "unmatched_labels": [lab["label_id"] for j, lab in enumerate(labels) if j not in used_l],
        "per_field": {f: {"hits": h, "scored": n} for f, (h, n) in per_field.items() if n},
        "mismatches": mismatches,
    }


def load_labels(case: str, include_unconfirmed: bool = True) -> List[dict]:
    data = json.loads((CASES_DIR / case / "expected_surface.json").read_text(encoding="utf-8"))
    return [e for e in data["events"] if include_unconfirmed or e.get("confirmed")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", required=True)
    parser.add_argument("--records", required=True, type=Path, help="a change_records.json")
    parser.add_argument("--tol", type=float, default=0.5)
    parser.add_argument("--confirmed-only", action="store_true")
    args = parser.parse_args()

    records = json.loads(args.records.read_text(encoding="utf-8"))
    result = score_surface(records, load_labels(args.case, not args.confirmed_only), args.tol)
    print("records %d, labels %d, matched %d" % (result["n_records"], result["n_labels"], result["matched"]))
    for field, c in result["per_field"].items():
        print("  %-9s %d / %d" % (field, c["hits"], c["scored"]))
    for m in result["mismatches"]:
        print("  MISS %s %s %s: expected %r, got %r" % (m["label"], m["event"], m["field"], m["expected"], m["actual"]))
    if result["unmatched_labels"]:
        print("  labels with no matching event:", ", ".join(result["unmatched_labels"]))


if __name__ == "__main__":
    main()
