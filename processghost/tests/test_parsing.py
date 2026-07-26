"""Unit tests for core/llm/parsing.py -- no API key or network call required."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.llm.parsing import (
    LLMResponseParseError,
    extract_chat_content,
    parse_action_response,
    parse_intent_response,
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


# --- parse_intent_response -------------------------------------------------

def _segment(**overrides):
    seg = {"id": 3, "start": 10.0, "end": 14.5, "text": "We go to the master sheet."}
    seg.update(overrides)
    return seg


def test_parse_intent_response_happy_path():
    raw = json.dumps(
        {
            "intent": "Navigate to the master sheet",
            "verb": "open",
            "obj": "the master sheet",
            "confidence": 0.82,
            "is_vague": True,
        }
    )
    token = parse_intent_response(raw, seg=_segment())

    assert token.intent_id == "intent_3"
    assert token.t_s == 10.0
    assert token.span_s == pytest.approx(4.5)
    assert token.raw_text == "We go to the master sheet."
    assert token.intent == "Navigate to the master sheet"
    assert token.verb == "open"
    assert token.obj == "the master sheet"
    assert token.confidence == pytest.approx(0.82)
    assert token.is_vague is True


def test_parse_intent_response_null_verb_and_obj():
    raw = json.dumps({"intent": "", "verb": None, "obj": None, "confidence": 0.1, "is_vague": False})
    token = parse_intent_response(raw, seg=_segment())
    assert token.verb is None
    assert token.obj is None


def test_parse_intent_response_falls_back_to_segment_text_when_intent_missing():
    raw = json.dumps({"confidence": 0.5, "is_vague": False})
    token = parse_intent_response(raw, seg=_segment())
    assert token.intent == "We go to the master sheet."


def test_parse_intent_response_malformed_json_raises():
    with pytest.raises(LLMResponseParseError):
        parse_intent_response("not json at all {{{", seg=_segment())


def test_parse_intent_response_non_object_json_raises():
    with pytest.raises(LLMResponseParseError):
        parse_intent_response("[1, 2, 3]", seg=_segment())


def test_parse_intent_response_bad_field_type_raises():
    raw = json.dumps({"intent": "x", "confidence": "not-a-number", "is_vague": False})
    with pytest.raises(LLMResponseParseError):
        parse_intent_response(raw, seg=_segment())


def test_parse_intent_response_uses_start_as_id_when_id_missing():
    seg = {"start": 7.0, "end": 9.0, "text": "hi"}
    raw = json.dumps({"intent": "hi", "confidence": 0.5, "is_vague": False})
    token = parse_intent_response(raw, seg=seg)
    assert token.intent_id == "intent_7"


# --- parse_action_response -------------------------------------------------

def test_parse_action_response_happy_path():
    raw = json.dumps(
        {
            "action_type": "switch_tab",
            "target": "Monthly Actuals tab",
            "details": "The user clicked the Monthly Actuals sheet tab.",
            "app": "Excel",
            "confidence": 0.77,
        }
    )
    token = parse_action_response(raw, idx=2, frame_path=Path("frame_0002.png"), t_s=4.0)

    assert token.action_id == "action_0002"
    assert token.t_s == 4.0
    assert token.frame_path == Path("frame_0002.png")
    assert token.action_type == "switch_tab"
    assert token.target == "Monthly Actuals tab"
    assert token.details == "The user clicked the Monthly Actuals sheet tab."
    assert token.app == "Excel"
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
