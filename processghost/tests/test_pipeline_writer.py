"""Unit tests for core/pipeline.py's Writer stage (_write_sop) -- the v2 pipeline's one FATAL
stage. Uses the shared _FakeClient (tests/fakes.py) so no network call or API key is needed.

Phase 3 note: the Writer now calls client.structured_chat(...) (LangChain-backed for OpenAI,
JSON-mode-by-prompt for Sarvam -- see core/llm/client.py / core/llm/sarvam_client.py) instead of
client.chat() + extract_chat_content() + a hand-rolled JSON parser. _FakeClient.structured_chat
returns a WriterOutput object directly (or raises), so these tests build WriterOutput objects
rather than JSON strings.
"""
from __future__ import annotations

import pytest

from core.models.sop import WriterOutput
from core.pipeline import WriterFailedError, _write_sop
from tests.fakes import _FakeClient


SEGMENTS = [
    {"id": 0, "start": 0.0, "end": 3.0, "text": "We open the reference sheet."},
    {"id": 1, "start": 3.0, "end": 6.0, "text": "Then it saves automatically."},
]


def _good_writer_output() -> WriterOutput:
    return WriterOutput.model_validate(
        {
            "steps": [
                {
                    "step_id": "step-01",
                    "order": 1,
                    "title": "Open the reference sheet",
                    "instruction": "Open the reference sheet.",
                    "attributions": [{"t_s": 0.0, "segment_ids": [0]}],
                },
                {
                    "step_id": "step-02",
                    "order": 2,
                    "title": "Save",
                    "instruction": "It saves automatically.",
                    "attributions": [{"t_s": 3.0, "segment_ids": [1]}],
                },
            ],
            "appendix_segments": [],
        }
    )


def test_write_sop_builds_document_from_structured_output():
    client = _FakeClient(structured_response=_good_writer_output())

    doc = _write_sop(client, "run1", SEGMENTS)

    assert len(doc.steps) == 2
    assert doc.steps[0].title == "Open the reference sheet"
    assert doc.provenance_complete is True
    assert client.metrics.failures == []


def test_write_sop_raises_and_records_on_call_exception():
    client = _FakeClient(structured_response=RuntimeError("connection reset"))

    with pytest.raises(WriterFailedError):
        _write_sop(client, "run1", SEGMENTS)

    assert len(client.metrics.failures) == 1
    assert client.metrics.failures[0]["stage"] == "writer"


def test_write_sop_raises_and_records_when_output_does_not_validate():
    """Mirrors the old "malformed JSON" case: with LangChain's json_schema mode, an output that
    can't be coerced into WriterOutput surfaces as structured_chat raising, not as a parse step
    downstream -- see core/llm/client.py: structured_chat's parsing_error handling."""
    client = _FakeClient(
        structured_response=RuntimeError("structured output did not match WriterOutput: bad shape")
    )

    with pytest.raises(WriterFailedError):
        _write_sop(client, "run1", SEGMENTS)

    assert len(client.metrics.failures) == 1
    assert client.metrics.failures[0]["stage"] == "writer"


def test_write_sop_raises_when_a_step_has_no_attribution():
    bad_output = WriterOutput.model_validate(
        {
            "steps": [
                {
                    "step_id": "step-01",
                    "order": 1,
                    "title": "Open the reference sheet",
                    "instruction": "Open the reference sheet.",
                    "attributions": [],
                }
            ],
            "appendix_segments": [
                {"segment_id": 0, "t_s": 0.0, "text": SEGMENTS[0]["text"], "reason": ""},
                {"segment_id": 1, "t_s": 3.0, "text": SEGMENTS[1]["text"], "reason": ""},
            ],
        }
    )
    client = _FakeClient(structured_response=bad_output)

    with pytest.raises(WriterFailedError):
        _write_sop(client, "run1", SEGMENTS)

    assert len(client.metrics.failures) == 1
    assert client.metrics.failures[0]["stage"] == "writer"


def test_write_sop_raises_when_a_segment_is_unaccounted_for():
    # Segment 1 appears nowhere -- neither in a step's attributions nor in appendix_segments.
    bad_output = WriterOutput.model_validate(
        {
            "steps": [
                {
                    "step_id": "step-01",
                    "order": 1,
                    "title": "Open the reference sheet",
                    "instruction": "Open the reference sheet.",
                    "attributions": [{"t_s": 0.0, "segment_ids": [0]}],
                }
            ],
            "appendix_segments": [],
        }
    )
    client = _FakeClient(structured_response=bad_output)

    with pytest.raises(WriterFailedError):
        _write_sop(client, "run1", SEGMENTS)

    assert len(client.metrics.failures) == 1
    assert client.metrics.failures[0]["stage"] == "writer"
