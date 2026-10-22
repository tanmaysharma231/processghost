"""Unit tests for core/llm/parsing.py -- no API key or network call required.

Phase 3 cleanup note: parse_transcript_extraction_response/parse_cross_check_response/
parse_action_response and their tests were deleted alongside the v1 pipeline they served
(core/align/, core/rules/) -- see core/pipeline.py's module docstring. build_sop_from_writer_output
and merge_reviewer_output (the v2 replacements) are covered in tests/test_pipeline_writer.py and
tests/test_pipeline_reviewer.py respectively, since they take a validated structured-output object
rather than a raw JSON string.
"""
from __future__ import annotations

import pytest

from core.llm.parsing import LLMResponseParseError, extract_chat_content, parse_sarvam_stt_result


# --- extract_chat_content -------------------------------------------------

def test_extract_chat_content_happy_path():
    response = {"choices": [{"message": {"content": "hello world"}}]}
    assert extract_chat_content(response) == "hello world"


def test_extract_chat_content_missing_choices_raises():
    with pytest.raises(LLMResponseParseError):
        extract_chat_content({})


# --- parse_sarvam_stt_result -------------------------------------------------

def test_parse_sarvam_stt_result_happy_path_with_timestamps():
    result = {
        "transcript": "First chunk. Second chunk.",
        "timestamps": {
            "chunks": ["First chunk.", "Second chunk."],
            "start_time_seconds": [0.0, 3.2],
            "end_time_seconds": [3.1, 6.5],
        },
    }
    segments = parse_sarvam_stt_result(result)

    assert len(segments) == 2
    assert segments[0] == {"id": 0, "start": 0.0, "end": 3.1, "text": "First chunk."}
    assert segments[1] == {"id": 1, "start": 3.2, "end": 6.5, "text": "Second chunk."}


def test_parse_sarvam_stt_result_missing_timestamps_falls_back_to_one_segment():
    result = {"transcript": "Everything in one blob."}
    segments = parse_sarvam_stt_result(result)

    assert len(segments) == 1
    assert segments[0]["text"] == "Everything in one blob."
    assert segments[0]["start"] == 0.0


def test_parse_sarvam_stt_result_mismatched_array_lengths_falls_back():
    result = {
        "transcript": "Whole transcript text.",
        "timestamps": {
            "chunks": ["a", "b"],
            "start_time_seconds": [0.0],  # shorter than chunks -- inconsistent
            "end_time_seconds": [1.0, 2.0],
        },
    }
    segments = parse_sarvam_stt_result(result)

    assert len(segments) == 1
    assert segments[0]["text"] == "Whole transcript text."


def test_parse_sarvam_stt_result_empty_chunks_falls_back():
    result = {
        "transcript": "text",
        "timestamps": {"chunks": [], "start_time_seconds": [], "end_time_seconds": []},
    }
    segments = parse_sarvam_stt_result(result)
    assert len(segments) == 1


def test_parse_sarvam_stt_result_no_transcript_key_does_not_raise():
    segments = parse_sarvam_stt_result({})
    assert len(segments) == 1
    assert segments[0]["text"] == ""
