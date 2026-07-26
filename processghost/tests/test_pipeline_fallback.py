"""Unit tests for the LLM-call-with-heuristic-fallback wrappers in core/pipeline.py.

Uses a fake stand-in for LLMClient so no network call or API key is needed, and so the fallback
path (bad JSON, or the call itself raising) can be exercised deterministically.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from core.llm.client import Metrics
from core.pipeline import _action_from_frame, _intent_from_segment


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


SEGMENT = {"id": 1, "start": 0.0, "end": 3.0, "text": "We open the master sheet."}


def test_intent_from_segment_uses_llm_result_on_success():
    good = _openai_response(
        '{"intent": "Open the master sheet", "verb": "open", "obj": "master sheet", '
        '"confidence": 0.9, "is_vague": false}'
    )
    client = _FakeClient(chat_response=good)

    token = _intent_from_segment(client, SEGMENT)

    assert token.intent == "Open the master sheet"
    assert token.verb == "open"
    assert client.metrics.failures == []


def test_intent_from_segment_falls_back_on_call_exception():
    client = _FakeClient(chat_response=RuntimeError("connection reset"))

    token = _intent_from_segment(client, SEGMENT)

    # heuristic fallback: raw text becomes the intent, no vague keywords present
    assert token.intent == SEGMENT["text"]
    assert token.is_vague is False
    assert len(client.metrics.failures) == 1
    assert client.metrics.failures[0]["stage"] == "intent_extraction"


def test_intent_from_segment_falls_back_on_malformed_json():
    client = _FakeClient(chat_response=_openai_response("not json"))

    token = _intent_from_segment(client, SEGMENT)

    assert token.intent == SEGMENT["text"]
    assert len(client.metrics.failures) == 1


def test_intent_from_segment_skips_call_for_empty_text():
    client = _FakeClient(chat_response=RuntimeError("should never be called"))
    seg = {"id": 2, "start": 5.0, "end": 6.0, "text": "   "}

    token = _intent_from_segment(client, seg)

    assert token.intent == "   "
    assert client.metrics.failures == []  # never attempted the call, so no failure recorded


def test_action_from_frame_uses_llm_result_on_success(tmp_path):
    frame = _write_test_frame(tmp_path / "frame_0000.png")
    good = _openai_response(
        '{"action_type": "filter", "target": "Status column", "details": "User applied a filter.", '
        '"app": "Excel", "confidence": 0.6}'
    )
    client = _FakeClient(vision_response=good)

    token = _action_from_frame(client, 0, frame, 0.0)

    assert token.action_type == "filter"
    assert token.target == "Status column"
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
