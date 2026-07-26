"""End-to-end integration test against a real video, gated behind two prerequisites (see
Process_ghost/docs/11-open-questions.md #6):

1. A real OPENAI_API_KEY in the environment.
2. A short fixture walkthrough video at tests/fixtures/sample_walkthrough.mp4.

If either is absent, this test is skipped rather than failing the suite.

This test does NOT assert that the pipeline finds any specific gap from
Process_ghost/docs/12-real-scenario-target.md -- that document is a validation fixture, not a
spec to encode into an assertion (see the plan history for why: a test that greps for this
scenario's specific strings would be exactly the overfitting this pipeline is trying to avoid).
Instead it writes a gap-coverage report next to the run's other artifacts for manual comparison.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURE_VIDEO = Path(__file__).parent / "fixtures" / "sample_walkthrough.mp4"

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") or not FIXTURE_VIDEO.exists(),
    reason=(
        "Integration test requires OPENAI_API_KEY and a fixture video at "
        f"{FIXTURE_VIDEO} -- neither is guaranteed to be present. See "
        "Process_ghost/docs/11-open-questions.md #6."
    ),
)


def test_run_pipeline_end_to_end_on_real_video():
    from core.pipeline import run_pipeline

    artifacts = run_pipeline(FIXTURE_VIDEO)

    assert artifacts.transcript, "expected at least one transcript segment"
    assert artifacts.actions, "expected at least one extracted frame/action"
    # At least one action should have gone through the real vision path (not the "unknown" stub)
    # for this test to be meaningful.
    assert any(a.action_type != "unknown" for a in artifacts.actions)
    assert artifacts.report_md and artifacts.report_md.exists()

    # Evidence, not a spec: write out what the two new generic signals actually found, for manual
    # comparison against docs/12-real-scenario-target.md's six-gap table. A missing gap here is
    # not a test failure -- it's a prompt of "does this need a generic fix," per the plan history.
    coverage = {
        "unresolved_entities": [f.model_dump() for f in artifacts.unresolved_entities],
        "unconfirmed_claims": [f.model_dump() for f in artifacts.unconfirmed_claims],
        "dissonance_kinds": [d.kind for d in artifacts.dissonances],
        "llm_fallbacks": artifacts.metrics.get("fallbacks", []),
    }
    coverage_path = artifacts.report_md.parent / "gap_coverage.json"
    coverage_path.write_text(json.dumps(coverage, indent=2, default=str), encoding="utf-8")
