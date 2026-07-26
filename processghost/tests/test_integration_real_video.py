"""End-to-end integration test against a real video, gated behind two prerequisites this repo does
not yet have (see Process_ghost/docs/11-open-questions.md #6):

1. A real OPENAI_API_KEY in the environment.
2. A short fixture walkthrough video at tests/fixtures/sample_walkthrough.mp4.

Both are currently absent, so this test is skipped rather than failing the suite. Once a real
fixture video is sourced, drop it at the path below and set OPENAI_API_KEY to enable it.
"""
from __future__ import annotations

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
