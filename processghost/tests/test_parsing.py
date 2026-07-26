"""Unit tests for core/llm/parsing.py -- no API key or network call required."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.llm.parsing import (
    LLMResponseParseError,
    extract_chat_content,
    parse_action_response,
    parse_cross_check_response,
    parse_transcript_extraction_response,
)


# --- extract_chat_content -------------------------------------------------

def test_extract_chat_content_happy_path():
    response = {"choices": [{"message": {"content": "hello world"}}]}
    assert extract_chat_content(response) == "hello world"


def test_extract_chat_content_missing_choices_raises():
    with pytest.raises(LLMResponseParseError):
        extract_chat_content({})


def test_extract_chat_content_empty_choices_raises():
    with pytest.raises(LLMResponseParseError):
        extract_chat_content({"choices": []})


# --- parse_transcript_extraction_response -----------------------------------

def _segments(n=1):
    base = [
        {"id": 0, "start": 0.0, "end": 4.0, "text": "First segment of narration."},
        {"id": 1, "start": 4.0, "end": 9.5, "text": "Second segment of narration."},
    ]
    return base[:n]


def _item(**overrides):
    item = {
        "segment_index": 0,
        "intent": "do the thing",
        "verb": "open",
        "obj": "the reference file",
        "confidence": 0.8,
        "is_vague": True,
        "entities_referenced": [
            {"raw_mention": "the reference file", "entity_role": "location_or_container", "named_specifically": False}
        ],
        "selection_criteria": None,
        "claimed_outcome": None,
    }
    item.update(overrides)
    return item


def test_parse_transcript_extraction_response_happy_path_single_segment():
    raw = json.dumps({"segments": [_item()]})
    tokens = parse_transcript_extraction_response(raw, _segments(1))

    assert len(tokens) == 1
    token = tokens[0]
    assert token.intent_id == "intent_0"
    assert token.t_s == 0.0
    assert token.span_s == pytest.approx(4.0)
    assert token.raw_text == "First segment of narration."
    assert token.intent == "do the thing"
    assert token.verb == "open"
    assert token.obj == "the reference file"
    assert token.confidence == pytest.approx(0.8)
    assert token.is_vague is True
    assert len(token.entities_referenced) == 1
    assert token.entities_referenced[0].raw_mention == "the reference file"
    assert token.entities_referenced[0].entity_role == "location_or_container"
    assert token.entities_referenced[0].named_specifically is False
    assert token.selection_criteria is None
    assert token.claimed_outcome is None


def test_parse_transcript_extraction_response_multiple_segments_in_order():
    raw = json.dumps(
        {
            "segments": [
                _item(segment_index=0, intent="first"),
                _item(segment_index=1, intent="second", entities_referenced=[]),
            ]
        }
    )
    tokens = parse_transcript_extraction_response(raw, _segments(2))

    assert [t.intent for t in tokens] == ["first", "second"]
    assert tokens[0].intent_id == "intent_0"
    assert tokens[1].intent_id == "intent_1"
    assert tokens[1].entities_referenced == []


def test_parse_transcript_extraction_response_carries_selection_criteria_and_claimed_outcome():
    raw = json.dumps(
        {
            "segments": [
                _item(
                    selection_criteria="match by the reference number",
                    claimed_outcome="the total updates automatically",
                )
            ]
        }
    )
    tokens = parse_transcript_extraction_response(raw, _segments(1))
    assert tokens[0].selection_criteria == "match by the reference number"
    assert tokens[0].claimed_outcome == "the total updates automatically"


def test_parse_transcript_extraction_response_malformed_json_raises():
    with pytest.raises(LLMResponseParseError):
        parse_transcript_extraction_response("not json {{{", _segments(1))


def test_parse_transcript_extraction_response_non_object_json_raises():
    with pytest.raises(LLMResponseParseError):
        parse_transcript_extraction_response("[1, 2, 3]", _segments(1))


def test_parse_transcript_extraction_response_missing_segments_key_raises():
    with pytest.raises(LLMResponseParseError):
        parse_transcript_extraction_response(json.dumps({"not_segments": []}), _segments(1))


def test_parse_transcript_extraction_response_wrong_length_raises():
    raw = json.dumps({"segments": [_item()]})
    with pytest.raises(LLMResponseParseError):
        parse_transcript_extraction_response(raw, _segments(2))  # 2 input segments, 1 item back


def test_parse_transcript_extraction_response_bad_field_type_raises():
    raw = json.dumps({"segments": [_item(confidence="not-a-number")]})
    with pytest.raises(LLMResponseParseError):
        parse_transcript_extraction_response(raw, _segments(1))


def test_parse_transcript_extraction_response_bad_entity_shape_raises():
    raw = json.dumps({"segments": [_item(entities_referenced=["not a dict"])]})
    with pytest.raises(LLMResponseParseError):
        parse_transcript_extraction_response(raw, _segments(1))


# --- parse_cross_check_response ---------------------------------------------

def test_parse_cross_check_response_happy_path():
    raw = json.dumps(
        {
            "unresolved_entities": [
                {"intent_id": "intent_0", "raw_mention": "the reference file", "reason": "never named specifically"}
            ],
            "unconfirmed_claims": [
                {"intent_id": "intent_1", "claimed_outcome": "it saves automatically", "reason": "no save action observed"}
            ],
        }
    )
    unresolved, unconfirmed = parse_cross_check_response(raw)

    assert len(unresolved) == 1
    assert unresolved[0].intent_id == "intent_0"
    assert unresolved[0].raw_mention == "the reference file"
    assert len(unconfirmed) == 1
    assert unconfirmed[0].intent_id == "intent_1"
    assert unconfirmed[0].claimed_outcome == "it saves automatically"


def test_parse_cross_check_response_empty_arrays():
    raw = json.dumps({"unresolved_entities": [], "unconfirmed_claims": []})
    unresolved, unconfirmed = parse_cross_check_response(raw)
    assert unresolved == []
    assert unconfirmed == []


def test_parse_cross_check_response_missing_arrays_default_empty():
    raw = json.dumps({})
    unresolved, unconfirmed = parse_cross_check_response(raw)
    assert unresolved == []
    assert unconfirmed == []


def test_parse_cross_check_response_malformed_json_raises():
    with pytest.raises(LLMResponseParseError):
        parse_cross_check_response("not json {{{")


def test_parse_cross_check_response_non_object_json_raises():
    with pytest.raises(LLMResponseParseError):
        parse_cross_check_response("[1, 2, 3]")


def test_parse_cross_check_response_missing_intent_id_raises():
    raw = json.dumps({"unresolved_entities": [{"raw_mention": "x", "reason": "y"}], "unconfirmed_claims": []})
    with pytest.raises(LLMResponseParseError):
        parse_cross_check_response(raw)


# --- parse_action_response -------------------------------------------------

def test_parse_action_response_happy_path():
    raw = json.dumps(
        {
            "action_type": "switch_tab",
            "target": "a specific tab",
            "details": "The user clicked a tab.",
            "app": "a spreadsheet application",
            "confidence": 0.77,
        }
    )
    token = parse_action_response(raw, idx=2, frame_path=Path("frame_0002.png"), t_s=4.0)

    assert token.action_id == "action_0002"
    assert token.t_s == 4.0
    assert token.frame_path == Path("frame_0002.png")
    assert token.action_type == "switch_tab"
    assert token.target == "a specific tab"
    assert token.details == "The user clicked a tab."
    assert token.app == "a spreadsheet application"
    assert token.confidence == pytest.approx(0.77)


def test_parse_action_response_defaults_action_type_to_unknown():
    raw = json.dumps({"confidence": 0.1})
    token = parse_action_response(raw, idx=0, frame_path=Path("f.png"), t_s=0.0)
    assert token.action_type == "unknown"
    assert token.target is None
    assert token.app is None


def test_parse_action_response_malformed_json_raises():
    with pytest.raises(LLMResponseParseError):
        parse_action_response("{not valid", idx=0, frame_path=Path("f.png"), t_s=0.0)


def test_parse_action_response_non_object_json_raises():
    with pytest.raises(LLMResponseParseError):
        parse_action_response('"just a string"', idx=0, frame_path=Path("f.png"), t_s=0.0)


def test_parse_action_response_bad_field_type_raises():
    raw = json.dumps({"action_type": "open_file", "confidence": "high"})
    with pytest.raises(LLMResponseParseError):
        parse_action_response(raw, idx=0, frame_path=Path("f.png"), t_s=0.0)
