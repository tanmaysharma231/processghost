"""End-to-end integration test against a real video, Sarvam provider path, gated behind two
prerequisites:

1. A real SARVAM_API_KEY in the environment.
2. A short fixture walkthrough video at tests/fixtures/sample_walkthrough.mp4.

If either is absent, this test is skipped rather than failing the suite -- no Sarvam key existed
as of writing this, so this test is expected to stay skipped until one is added. See
tests/test_integration_real_video.py for the OpenAI-path equivalent, the exact assertions this
mirrors, and for why each integration test pins its own provider rather than trusting the global
default.

The Sarvam STT batch result shape (core/llm/sarvam_client.py: parse_sarvam_stt_result's expected
"timestamps" structure) was implemented from documentation, not a real response -- see that
module's docstring for what's unverified. This test is the first real check of that assumption for
the v2 Writer stage; expect to need adjustments in core/llm/sarvam_client.py the first time this
actually runs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURE_VIDEO = Path(__file__).parent / "fixtures" / "sample_walkthrough.mp4"

NEVER_STATED = ["Column F", "Bill ID", "Paid"]

pytestmark = pytest.mark.skipif(
    not os.getenv("SARVAM_API_KEY") or not FIXTURE_VIDEO.exists(),
    reason=(
        "Integration test requires SARVAM_API_KEY and a fixture video at "
        f"{FIXTURE_VIDEO} -- neither is guaranteed to be present."
    ),
)


def test_writer_drafts_sop_from_real_video_sarvam():
    from core import config
    from core.pipeline import run_pipeline

    original_provider = config.LLM_PROVIDER
    config.LLM_PROVIDER = "sarvam"
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
            "narration."
        )

    coverage = {
        "steps": [s.model_dump() for s in artifacts.sop.steps],
        "appendix_segments": [s.model_dump() for s in artifacts.sop.appendix_segments],
        "llm_fallbacks": artifacts.metrics.get("fallbacks", []),
        "timing_degraded": artifacts.sop.timing_degraded,
    }
    coverage_path = artifacts.sop_draft_md.parent / "writer_coverage.json"
    coverage_path.write_text(json.dumps(coverage, indent=2, default=str), encoding="utf-8")

    # Unlike the OpenAI test, explicitly surface fallback count and timing_degraded here rather
    # than only the hard assertions above -- the Sarvam STT path is expected to be noisier on
    # first real use (unverified response shape, see module docstring), and both signals are
    # exactly what sarvam_client.py's unverified parsing needs correcting against.
    print(f"Sarvam path fallbacks this run: {len(artifacts.metrics.get('fallbacks', []))}")
