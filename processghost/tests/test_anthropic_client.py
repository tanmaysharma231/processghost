"""The Anthropic provider with a fake ChatAnthropic: no network, no key. These tests check how the client
calls LangChain and records the result; they cannot show the live API accepts the request."""
from __future__ import annotations

import cv2
import numpy as np
import pytest
from pydantic import BaseModel

from core import config, pipeline
from core.llm import anthropic_client, cache
from core.llm.anthropic_client import AnthropicClient
from core.llm.client import LLMClient
from core.llm.retry import StructuredOutputError, is_transient


class Answer(BaseModel):
    value: int


class _Raw:
    usage_metadata = {"input_tokens": 1000, "output_tokens": 200}


class _FakeChat:
    instances = []
    result = {"raw": _Raw(), "parsed": Answer(value=7), "parsing_error": None}

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.structured = None
        self.messages = None
        _FakeChat.instances.append(self)

    def with_structured_output(self, schema, **kwargs):
        self.structured = (schema, kwargs)
        return self

    def invoke(self, messages):
        self.messages = messages
        return _FakeChat.result


@pytest.fixture(autouse=True)
def _fake(monkeypatch, tmp_path):
    _FakeChat.instances = []
    _FakeChat.result = {"raw": _Raw(), "parsed": Answer(value=7), "parsing_error": None}
    monkeypatch.setattr(anthropic_client, "ChatAnthropic", _FakeChat)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "CACHE_ENABLED", True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_call_uses_native_structured_output_and_sends_no_temperature():
    out = AnthropicClient().structured_chat("sys", "user", Answer, stage="writer")
    chat = _FakeChat.instances[0]
    assert out == Answer(value=7)
    assert chat.structured[1] == {"method": "json_schema", "include_raw": True}  # not forced tool calling
    assert "temperature" not in chat.kwargs and "top_p" not in chat.kwargs
    assert chat.kwargs["model"] == config.ANTHROPIC_MODEL_TEXT and chat.kwargs["max_retries"] == 0


def test_usage_is_priced_from_the_claude_table():
    client = AnthropicClient()
    client.structured_chat("sys", "user", Answer, stage="writer")
    expected = 1000 / 1e6 * 2.00 + 200 / 1e6 * 10.00
    assert client.metrics.cost_usd == pytest.approx(expected)
    assert client.metrics.records[0]["model"] == "claude-sonnet-5-5" and "pricing" not in client.metrics.records[0].get("meta", {})


def test_second_identical_call_is_served_from_the_cache_for_free():
    client = AnthropicClient()
    client.structured_chat("sys", "user", Answer, stage="writer")
    cost = client.metrics.cost_usd
    again = client.structured_chat("sys", "user", Answer, stage="writer")
    assert again == Answer(value=7) and len(_FakeChat.instances) == 1 and client.metrics.cost_usd == cost


def test_cache_entries_are_separate_from_openai_entries():
    a = cache.make_key("structured", "anthropic", "m", "s", "u", "Answer", {})
    b = cache.make_key("structured", "openai", "m", "s", "u", "Answer", {})
    assert a != b


def test_images_are_attached_as_base64_blocks(tmp_path):
    img = tmp_path / "f.png"
    cv2.imwrite(str(img), np.full((20, 30, 3), 128, dtype=np.uint8))
    AnthropicClient().structured_chat("sys", "user", Answer, images=[img], stage="checker")
    blocks = _FakeChat.instances[0].messages[1].content
    assert [b["type"] for b in blocks] == ["text", "image"] and blocks[1]["mime_type"] == "image/png" and blocks[1]["base64"]


def test_a_schema_mismatch_is_reported_as_structured_output_error(monkeypatch):
    monkeypatch.setattr(config, "RETRY_MAX_PARSE_ATTEMPTS", 1)
    _FakeChat.result = {"raw": _Raw(), "parsed": None, "parsing_error": ValueError("bad")}
    with pytest.raises(StructuredOutputError):
        AnthropicClient().structured_chat("sys", "user", Answer, stage="writer")


def test_openai_key_is_not_needed_until_a_transcription_is_requested():
    client = AnthropicClient()  # OPENAI_API_KEY was removed by the fixture
    assert client._openai is None


def test_pipeline_factory_picks_the_provider(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    assert isinstance(pipeline._make_client(pipeline.Metrics()), AnthropicClient)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    assert type(pipeline._make_client(pipeline.Metrics())) is LLMClient


def test_anthropic_overload_and_connection_errors_are_retried_but_a_bad_request_is_not():
    import anthropic
    import httpx

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    assert is_transient(anthropic.APIConnectionError(request=req))
    assert is_transient(anthropic.APITimeoutError(request=req))
    assert is_transient(anthropic.InternalServerError("overloaded", response=httpx.Response(529, request=req), body=None))
    assert not is_transient(anthropic.BadRequestError("credit balance is too low", response=httpx.Response(400, request=req), body=None))


def test_a_response_with_no_usage_is_recorded_as_a_zero_cost_call_not_a_crash():
    class _NoUsage:
        usage_metadata = None

    _FakeChat.result = {"raw": _NoUsage(), "parsed": Answer(value=7), "parsing_error": None}
    client = AnthropicClient()
    assert client.structured_chat("sys", "user", Answer, stage="writer") == Answer(value=7)
    assert client.metrics.tokens_in == 0 and client.metrics.tokens_out == 0 and client.metrics.cost_usd == 0.0
