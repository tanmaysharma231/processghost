"""End-to-end integration test against a real video, OpenAI provider path, gated behind two
prerequisites (see Process_ghost/docs/11-open-questions.md #6):

1. A real OPENAI_API_KEY in the environment.
2. A short fixture walkthrough video at tests/fixtures/sample_walkthrough.mp4 (a copy of
   Process_ghost/data/english_hackathon_test.mp4 -- the real grounding scenario documented in
   docs/12-real-scenario-target.md).

If either is absent, this test is skipped rather than failing the suite.

run_pipeline runs the full Writer -> Reviewer -> Checker chain now, but this test only asserts
against artifacts.sop_draft_md -- the file written right after the Writer, before the Reviewer or
Checker ever touch the document -- so it stays a pure Writer-output check regardless of what the
later stages do. It does NOT hard-assert that the Writer surfaces any specific gap -- finding gaps
is the Reviewer's/Checker's job, not the Writer's, and per docs/06-known-gaps.md #6 a test that
greps for this scenario's specific answers would be exactly the overfitting this pipeline exists to
avoid. It writes a writer_coverage.json next to the run's other artifacts for manual comparison
against the 7 narrated actions listed in docs/13-current-logic-and-architecture.md section 2.

It DOES hard-assert the negative: the Writer must never mention "Column F", "Bill ID", or "Paid" --
none of that is stated in the narration, so if any of it appears, the Writer is hallucinating a
repair it was explicitly instructed not to make (see prompts.WRITER_SYSTEM). That's unambiguously a
bug, unlike the positive coverage question, which needs a human to judge.

Explicitly forces core.config.LLM_PROVIDER = "openai" for the duration of the test (restored
after) regardless of the module-level default.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURE_VIDEO = Path(__file__).parent / "fixtures" / "sample_walkthrough.mp4"

# Knowledge that is never stated in this specific recording's narration -- see
# docs/12-real-scenario-target.md section 2. If any of it appears in the Writer's draft, the
# Writer repaired a gap instead of transcribing faithfully, which is exactly what
# prompts.WRITER_SYSTEM instructs it not to do.
NEVER_STATED = ["Column F", "Bill ID", "Paid"]

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") or not FIXTURE_VIDEO.exists(),
    reason=(
        "Integration test requires OPENAI_API_KEY and a fixture video at "
        f"{FIXTURE_VIDEO} -- neither is guaranteed to be present. See "
        "Process_ghost/docs/11-open-questions.md #6."
    ),
)


def test_writer_drafts_sop_from_real_video():
    from core import config
    from core.pipeline import run_pipeline

    original_provider = config.LLM_PROVIDER
    config.LLM_PROVIDER = "openai"
    try:
        artifacts = run_pipeline(FIXTURE_VIDEO)
    finally:
        config.LLM_PROVIDER = original_provider

    assert artifacts.transcript, "expected at least one transcript segment"
    assert artifacts.sop is not None
    assert artifacts.sop.provenance_complete
    assert artifacts.sop_draft_md and artifacts.sop_draft_md.exists()

    draft_text = artifacts.sop_draft_md.read_text(encoding="utf-8")
    for phrase in NEVER_STATED:
        assert phrase.lower() not in draft_text.lower(), (
            f"Writer draft contains {phrase!r}, which is never stated in this recording's "
            "narration -- the Writer appears to have repaired a gap instead of transcribing "
            "faithfully."
        )

    # Evidence, not a spec: write out the extracted steps/appendix for manual comparison against
    # the 7 narrated actions in docs/13 section 2. Missing one here is a prompt-iteration signal,
    # not a test failure.
    coverage = {
        "steps": [s.model_dump() for s in artifacts.sop.steps],
        "appendix_segments": [s.model_dump() for s in artifacts.sop.appendix_segments],
        "llm_fallbacks": artifacts.metrics.get("fallbacks", []),
    }
    coverage_path = artifacts.sop_draft_md.parent / "writer_coverage.json"
    coverage_path.write_text(json.dumps(coverage, indent=2, default=str), encoding="utf-8")
