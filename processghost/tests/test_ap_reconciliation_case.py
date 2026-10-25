"""The draft ap_reconciliation eval case loads, its files agree with each other, and every pattern compiles.
The labels are DRAFTS (confirmed=false) written from the frames of data/test2.mkv, so this checks the
case is well formed, not that the labels are right."""
from __future__ import annotations

import json
import re
from pathlib import Path

from evals.score_classes import load_labels as load_classes
from evals.score_events import load_labels as load_events
from evals.score_findings import CASES_DIR, load_spec, score_text
from evals.score_surface import load_labels as load_surface

CASE = "ap_reconciliation"


def _patterns(spec: dict) -> list[str]:
    out = [p for f in spec["findings"] for g in f["groups"] for p in g]
    out += [p for f in spec["findings"] for p in f.get("exclude", [])]
    out += [x["pattern"] for x in spec.get("forbidden", [])]
    return out


def test_label_files_describe_the_same_events():
    events = load_events(CASE, include_unconfirmed=True)
    classes = load_classes(CASE)
    surface = load_surface(CASE)
    ids = [e["label_id"] for e in events]
    assert len(ids) == len(set(ids)) == 19
    assert [c["label_id"] for c in classes] == ids
    assert [s["label_id"] for s in surface] == ids
    for e, c, s in zip(events, classes, surface):
        assert (e["t_start"], e["t_end"]) == (c["t_start"], c["t_end"]) == (s["t_start"], s["t_end"])
        assert e["t_start"] < e["t_end"]


def test_the_three_data_changes_and_the_noise_are_labelled():
    classes = {c["label_id"]: c for c in load_classes(CASE)}
    assert sorted(k for k, c in classes.items() if c["class"] == "data_change") == ["L10", "L12", "L19"]
    noise = [k for k, c in classes.items() if c.get("noise")]
    assert noise == ["L13", "L14", "L15"]
    assert classes["L7"]["class"] == "view_only" and classes["L7"]["pointer_only"]
    assert all(classes[k]["class"] != "data_change" for k in noise)


def test_every_pattern_compiles_and_every_finding_has_a_group():
    spec = load_spec(CASE)
    assert [f["id"] for f in spec["findings"]] == ["G%d" % i for i in range(1, 12)]
    assert all(f["groups"] for f in spec["findings"])
    for p in _patterns(spec):
        re.compile(p, re.I)
    steps = json.loads((CASES_DIR / CASE / "expected_steps.json").read_text(encoding="utf-8"))
    for step in steps["steps"]:
        for g in step["groups"]:
            for p in g:
                re.compile(p, re.I)


def test_files_are_ascii_only():
    for path in (CASES_DIR / CASE).glob("*.json"):
        path.read_text(encoding="ascii")


def test_scoring_a_sample_text_hits_and_misses_as_designed():
    spec = load_spec(CASE)
    good = "\n".join([
        "Open the Exception Log sheet and check the variance, a formula.",
        "Question: what threshold decides whether a 2,000 variance can be accepted?",
        "Question: some exceptions are accepted automatically by the system; what decides that?",
    ])
    result = score_text(good, spec)
    surfaced = {r["id"] for r in result["findings"] if r["surfaced"]}
    assert {"G3", "G6", "G7", "G10"} <= surfaced
    assert result["forbidden_claims"] == []
    bad = "The variance is unacceptable. Audit INV-117 as well.\nNothing is missing"
    flagged = score_text(bad, spec)
    assert len(flagged["forbidden_claims"]) == 2 and flagged["false_completeness"]
    assert score_text("", spec)["n_surfaced"] == 0
