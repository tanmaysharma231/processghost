"""The unified LLMClient with a fake chat model: no network, no key. These tests check how the client
builds the model through LangChain and records the result; they cannot show a live API accepts the
request (python -m scripts.smoke_anthropic does that for Anthropic)."""
from __future__ import annotations

import cv2
import numpy as np
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from core import config, pipeline
from core.llm import cache
from core.llm import client as client_mod
from core.llm.client import LLMClient, Metrics
from core.llm.retry import BudgetExceededError, StructuredOutputError, is_transient


class Answer(BaseModel):
    value: int


class _Raw:
    usage_metadata = {"input_tokens": 1000, "output_tokens": 200}


class _FakeChat:
    instances = []
    result = {"raw": _Raw(), "parsed": Answer(value=7), "parsing_error": None}
    on_invoke = None

    def __init__(self, model, model_provider, **kwargs):
        self.model, self.provider, self.kwargs = model, model_provider, kwargs
        self.structured = None
        self.messages = None
        _FakeChat.instances.append(self)

    def with_structured_output(self, schema, **kwargs):
        self.structured = (schema, kwargs)
        return self

    def invoke(self, messages):
        self.messages = messages
        if _FakeChat.on_invoke:
            _FakeChat.on_invoke(self)
        return _FakeChat.result


@pytest.fixture(autouse=True)
def _fake(monkeypatch, tmp_path):
    _FakeChat.instances = []
    _FakeChat.result = {"raw": _Raw(), "parsed": Answer(value=7), "parsing_error": None}
    _FakeChat.on_invoke = None
    monkeypatch.setattr(client_mod, "init_chat_model", lambda model, model_provider, **kw: _FakeChat(model, model_provider, **kw))
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "CACHE_ENABLED", False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _anthropic() -> LLMClient:
    return LLMClient(provider="anthropic")


def test_openai_call_pins_temperature_timeout_and_no_internal_retries():
    out = LLMClient(provider="openai").structured_chat("sys", "user", Answer, stage="writer")
    chat = _FakeChat.instances[0]
    assert out == Answer(value=7)
    assert chat.provider == "openai" and chat.model == config.MODEL_TEXT
    assert chat.structured[1] == {"method": "json_schema", "include_raw": True}
    assert chat.kwargs["temperature"] == config.LLM_TEMPERATURE == 0.0
    assert chat.kwargs["max_retries"] == 0 and chat.kwargs["timeout"] == config.LLM_TIMEOUT_S > 0


def test_anthropic_call_uses_native_structured_output_and_sends_no_temperature():
    out = _anthropic().structured_chat("sys", "user", Answer, stage="writer")
    chat = _FakeChat.instances[0]
    assert out == Answer(value=7)
    assert chat.provider == "anthropic" and chat.model == config.ANTHROPIC_MODEL_TEXT
    assert chat.structured[1] == {"method": "json_schema", "include_raw": True}  # not forced tool calling
    assert "temperature" not in chat.kwargs and "top_p" not in chat.kwargs
    assert chat.kwargs["max_tokens"] == config.ANTHROPIC_MAX_TOKENS and chat.kwargs["max_retries"] == 0


def test_images_pick_the_vision_model_and_an_explicit_model_wins(tmp_path):
    img = tmp_path / "f.png"
    cv2.imwrite(str(img), np.full((20, 30, 3), 128, dtype=np.uint8))
    LLMClient(provider="openai").structured_chat("s", "u", Answer, images=[img], stage="checker")
    _anthropic().structured_chat("s", "u", Answer, images=[img], stage="checker")
    LLMClient(provider="openai").structured_chat("s", "u", Answer, stage="writer", model="custom-model")
    assert [c.model for c in _FakeChat.instances] == [config.MODEL_VISION, config.ANTHROPIC_MODEL_VISION, "custom-model"]


def test_usage_is_priced_from_the_claude_table():
    client = _anthropic()
    client.structured_chat("sys", "user", Answer, stage="writer")
    expected = 1000 / 1e6 * 2.00 + 200 / 1e6 * 10.00
    assert client.metrics.cost_usd == pytest.approx(expected)
    rec = client.metrics.records[0]
    assert rec["model"] == config.ANTHROPIC_MODEL_TEXT and "pricing" not in rec.get("meta", {})


def test_images_are_attached_as_base64_blocks(tmp_path):
    img = tmp_path / "f.png"
    cv2.imwrite(str(img), np.full((20, 30, 3), 128, dtype=np.uint8))
    _anthropic().structured_chat("sys", "user", Answer, images=[img], stage="checker")
    blocks = _FakeChat.instances[0].messages[1].content
    assert [b["type"] for b in blocks] == ["text", "image"] and blocks[1]["mime_type"] == "image/png" and blocks[1]["base64"]


def test_a_schema_mismatch_is_reported_as_structured_output_error(monkeypatch):
    monkeypatch.setattr(config, "RETRY_MAX_PARSE_ATTEMPTS", 1)
    _FakeChat.result = {"raw": _Raw(), "parsed": None, "parsing_error": ValueError("bad")}
    with pytest.raises(StructuredOutputError):
        _anthropic().structured_chat("sys", "user", Answer, stage="writer")


def test_openai_key_is_not_needed_until_a_transcription_is_requested():
    client = _anthropic()  # OPENAI_API_KEY was removed by the fixture
    assert client._openai is None


def test_pipeline_factory_builds_one_client_for_the_configured_provider(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    assert pipeline._make_client(Metrics()).provider == "anthropic"
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    assert pipeline._make_client(Metrics()).provider == "openai"


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
    client = _anthropic()
    assert client.structured_chat("sys", "user", Answer, stage="writer") == Answer(value=7)
    assert client.metrics.tokens_in == 0 and client.metrics.tokens_out == 0 and client.metrics.cost_usd == 0.0


# --- disk cache: off by default, expensive stages only -----------------------------------------


def test_the_cache_applies_only_to_listed_stages_and_only_when_enabled(monkeypatch):
    client = LLMClient(provider="openai")
    client.structured_chat("s", "u", Answer, stage="checker")
    assert _FakeChat.instances[-1].kwargs["cache"] is False  # CACHE_ENABLED is False by default
    monkeypatch.setattr(config, "CACHE_ENABLED", True)
    client.structured_chat("s", "u", Answer, stage="checker")
    assert _FakeChat.instances[-1].kwargs["cache"] is client._disk_cache
    client.structured_chat("s", "u", Answer, stage="writer")
    client.structured_chat("s", "u", Answer, stage="reviewer")
    assert [c.kwargs["cache"] for c in _FakeChat.instances[-2:]] == [False, False]  # variance stages never cache


def test_a_cache_hit_is_recorded_as_free():
    client = _anthropic()
    _FakeChat.on_invoke = lambda chat: setattr(client._disk_cache, "hits", client._disk_cache.hits + 1)
    client.structured_chat("s", "u", Answer, stage="checker")
    assert client.metrics.cost_usd == 0.0 and client.metrics.records[-1]["meta"] == {"cached": True}


def test_an_answer_that_fails_validation_is_dropped_from_the_cache(monkeypatch):
    monkeypatch.setattr(config, "CACHE_ENABLED", True)
    monkeypatch.setattr(config, "RETRY_MAX_PARSE_ATTEMPTS", 1)
    client = _anthropic()
    key = cache.make_key("llm", "m", "p")

    def write_entry(chat):
        cache.put("llm", key, {"generations": []})
        client._disk_cache._last_key = key

    _FakeChat.on_invoke = write_entry
    _FakeChat.result = {"raw": _Raw(), "parsed": None, "parsing_error": ValueError("bad")}
    with pytest.raises(StructuredOutputError):
        client.structured_chat("s", "u", Answer, stage="checker")
    assert cache.get("llm", key) is None


def test_disk_cache_serves_the_second_identical_call_from_disk(monkeypatch):
    monkeypatch.setattr(config, "CACHE_ENABLED", True)
    disk = cache.DiskCache()
    replies = iter([AIMessage(content="first"), AIMessage(content="second")])
    model = GenericFakeChatModel(messages=replies, cache=disk)
    assert model.invoke([HumanMessage(content="hi")]).content == "first"
    assert model.invoke([HumanMessage(content="hi")]).content == "first"  # not "second": never reached the model
    assert disk.hits == 1
    assert model.invoke([HumanMessage(content="different")]).content == "second"  # any change is a miss
    assert disk.hits == 1
    fresh = GenericFakeChatModel(messages=iter([AIMessage(content="x")]), cache=cache.DiskCache())
    assert fresh.invoke([HumanMessage(content="hi")]).content == "first"  # survives a new process/cache object


def test_no_model_call_is_made_once_the_run_is_over_budget(monkeypatch):
    monkeypatch.setattr(config, "MAX_RUN_COST_USD", 1.0)
    client = _anthropic()
    client.metrics.cost_usd = 1.5
    with pytest.raises(BudgetExceededError):
        client.structured_chat("s", "u", Answer, stage="reconcile")
    assert _FakeChat.instances == []
    monkeypatch.setattr(config, "MAX_RUN_COST_USD", 0)  # 0 disables the limit
    assert client.structured_chat("s", "u", Answer, stage="reconcile") == Answer(value=7)
