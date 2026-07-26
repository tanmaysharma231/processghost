"""Unit tests for the LLM-call-with-fallback wrappers in core/pipeline.py.

Uses a fake stand-in for LLMClient so no network call or API key is needed, and so the fallback
path (bad JSON, wrong shape, or the call itself raising) can be exercised deterministically.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from core.llm.client import Metrics
from core.models.tokens import ActionToken, IntentToken
from core.pipeline import _action_from_frame, _cross_check_resolution, _extract_intents_from_transcript


def _write_test_frame(path: Path, size: tuple[int, int] = (32, 24)) -> Path:
    """Write a genuine tiny PNG -- frame_to_data_uri opens frames with PIL, so a real image is
    needed for the success-path tests (a fake byte string would raise before the vision call)."""
    Image.new("RGB", size, color=(10, 20, 30)).save(path, format="PNG")
    return path


class _FakeClient:
    """Stands in for LLMClient. `chat_response`/`vision_response` control what the call returns;
    set either to an Exception instance to make the call raise instead."""

    def __init__(self, chat_response=None, vision_response=None):
        self.metrics = Metrics()
        self._chat_response = chat_response
        self._vision_response = vision_response

    def chat(self, messages, **kwargs):
        if isinstance(self._chat_response, Exception):
            raise self._chat_response
        return self._chat_response

    def vision(self, messages, **kwargs):
        if isinstance(self._vision_response, Exception):
            raise self._vision_response
        return self._vision_response


def _openai_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


SEGMENTS = [
    {"id": 0, "start": 0.0, "end": 3.0, "text": "We open the reference sheet."},
    {"id": 1, "start": 3.0, "end": 6.0, "text": "Then it saves automatically."},
]


def _segment_item(**overrides):
    item = {
        "segment_index": 0,
        "intent": "open the reference sheet",
        "verb": "open",
        "obj": "the reference sheet",
        "confidence": 0.9,
        "is_vague": False,
        "entities_referenced": [],
        "selection_criteria": None,
        "claimed_outcome": None,
    }
    item.update(overrides)
    return item


# --- _extract_intents_from_transcript ---------------------------------------

def test_extract_intents_uses_llm_result_on_success():
    good = _openai_response(
        json.dumps(
            {
                "segments": [
                    _segment_item(segment_index=0, intent="open the reference sheet"),
                    _segment_item(segment_index=1, intent="save", claimed_outcome="it saves automatically"),
                ]
            }
        )
    )
    client = _FakeClient(chat_response=good)

    tokens = _extract_intents_from_transcript(client, SEGMENTS)

    assert len(tokens) == 2
    assert tokens[0].intent == "open the reference sheet"
    assert tokens[1].claimed_outcome == "it saves automatically"
    assert client.metrics.failures == []


def test_extract_intents_falls_back_to_heuristic_on_call_exception():
    client = _FakeClient(chat_response=RuntimeError("connection reset"))

    tokens = _extract_intents_from_transcript(client, SEGMENTS)

    assert len(tokens) == 2
    assert tokens[0].intent == SEGMENTS[0]["text"]
    assert tokens[1].intent == SEGMENTS[1]["text"]
    assert len(client.metrics.failures) == 1
    assert client.metrics.failures[0]["stage"] == "transcript_extraction"


def test_extract_intents_falls_back_to_heuristic_on_malformed_json():
    client = _FakeClient(chat_response=_openai_response("not json"))

    tokens = _extract_intents_from_transcript(client, SEGMENTS)

    assert len(tokens) == 2
    assert len(client.metrics.failures) == 1


def test_extract_intents_falls_back_to_heuristic_on_wrong_length():
    # Only one item back for two input segments -- whole batch must fall back, not partial.
    good = _openai_response(json.dumps({"segments": [_segment_item()]}))
    client = _FakeClient(chat_response=good)

    tokens = _extract_intents_from_transcript(client, SEGMENTS)

    assert len(tokens) == 2
    assert tokens[0].intent == SEGMENTS[0]["text"]
    assert len(client.metrics.failures) == 1


def test_extract_intents_returns_empty_list_for_no_segments():
    client = _FakeClient(chat_response=RuntimeError("should never be called"))
    tokens = _extract_intents_from_transcript(client, [])
    assert tokens == []
    assert client.metrics.failures == []


# --- _cross_check_resolution -------------------------------------------------

def _intent_with(**overrides) -> IntentToken:
    base = dict(
        intent_id="intent_0",
        t_s=0.0,
        span_s=3.0,
        raw_text="raw text",
        intent="an intent",
        confidence=0.8,
        is_vague=True,
        entities_referenced=[],
        selection_criteria=None,
        claimed_outcome=None,
    )
    base.update(overrides)
    return IntentToken(**base)


def _action_with(**overrides) -> ActionToken:
    base = dict(
        action_id="action_0000",
        t_s=1.0,
        frame_path=Path("frame_0000.png"),
        action_type="view",
        target=None,
        details="",
        app=None,
        confidence=0.9,
    )
    base.update(overrides)
    return ActionToken(**base)


def test_cross_check_uses_llm_result_on_success():
    intents = [_intent_with(claimed_outcome="it saves automatically")]
    actions = [_action_with()]
    good = _openai_response(
        json.dumps(
            {
                "unresolved_entities": [],
                "unconfirmed_claims": [
                    {"intent_id": "intent_0", "claimed_outcome": "it saves automatically", "reason": "no save action observed"}
                ],
            }
        )
    )
    client = _FakeClient(chat_response=good)

    unresolved, unconfirmed = _cross_check_resolution(client, intents, actions)

    assert unresolved == []
    assert len(unconfirmed) == 1
    assert unconfirmed[0].intent_id == "intent_0"
    assert client.metrics.failures == []


def test_cross_check_falls_back_to_empty_on_call_exception():
    intents = [_intent_with(claimed_outcome="it saves automatically")]
    actions = [_action_with()]
    client = _FakeClient(chat_response=RuntimeError("rate limited"))

    unresolved, unconfirmed = _cross_check_resolution(client, intents, actions)

    assert unresolved == []
    assert unconfirmed == []
    assert len(client.metrics.failures) == 1
    assert client.metrics.failures[0]["stage"] == "cross_check"


def test_cross_check_falls_back_to_empty_on_malformed_json():
    intents = [_intent_with(claimed_outcome="it saves automatically")]
    actions = [_action_with()]
    client = _FakeClient(chat_response=_openai_response("not json"))

    unresolved, unconfirmed = _cross_check_resolution(client, intents, actions)

    assert unresolved == []
    assert unconfirmed == []
    assert len(client.metrics.failures) == 1


def test_cross_check_skips_call_when_nothing_to_check():
    # No intent has entities/selection_criteria/claimed_outcome set -- nothing worth cross-checking.
    intents = [_intent_with()]
    actions = [_action_with()]
    client = _FakeClient(chat_response=RuntimeError("should never be called"))

    unresolved, unconfirmed = _cross_check_resolution(client, intents, actions)

    assert unresolved == []
    assert unconfirmed == []
    assert client.metrics.failures == []


# --- _action_from_frame (unchanged behavior, still exercised here) ----------

def test_action_from_frame_uses_llm_result_on_success(tmp_path):
    frame = _write_test_frame(tmp_path / "frame_0000.png")
    good = _openai_response(
        '{"action_type": "filter", "target": "a specific column", "details": "User applied a filter.", '
        '"app": "a spreadsheet application", "confidence": 0.6}'
    )
    client = _FakeClient(vision_response=good)

    token = _action_from_frame(client, 0, frame, 0.0)

    assert token.action_type == "filter"
    assert token.target == "a specific column"
    assert client.metrics.failures == []


def test_action_from_frame_falls_back_on_call_exception(tmp_path):
    frame = _write_test_frame(tmp_path / "frame_0001.png")
    client = _FakeClient(vision_response=RuntimeError("rate limited"))

    token = _action_from_frame(client, 1, frame, 2.0)

    assert token.action_type == "unknown"
    assert token.confidence == 0.3
    assert len(client.metrics.failures) == 1
    assert client.metrics.failures[0]["stage"] == "action_description"


def test_action_from_frame_falls_back_on_missing_file(tmp_path):
    # frame_to_data_uri will raise FileNotFoundError before any call is made
    missing = tmp_path / "does_not_exist.png"
    client = _FakeClient(vision_response=RuntimeError("should not be reached"))

    token = _action_from_frame(client, 2, missing, 4.0)

    assert token.action_type == "unknown"
    assert len(client.metrics.failures) == 1
