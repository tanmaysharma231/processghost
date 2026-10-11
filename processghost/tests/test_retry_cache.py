"""Unit tests for core/llm/retry.py (transient-only retry policy) and core/llm/cache.py
(content-addressed call cache), plus the cache hit path through LLMClient.structured_chat with a
fake chat model. No network, no API key."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel

from core import config
from core.llm import cache
from core.llm import client as client_mod
from core.llm.client import LLMClient, Metrics
from core.llm.retry import StructuredOutputError, is_transient, llm_retry


def _rate_limit() -> openai.RateLimitError:
    resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
    return openai.RateLimitError("slow down", response=resp, body=None)


def _bad_request() -> openai.BadRequestError:
    resp = httpx.Response(400, request=httpx.Request("POST", "http://x"))
    return openai.BadRequestError("nope", response=resp, body=None)


class _Caller:
    def __init__(self, failures):
        self.metrics = Metrics()
        self.failures = list(failures)
        self.calls = 0

    def go(self):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return "ok"


@pytest.fixture
def fast_retry(monkeypatch):
    monkeypatch.setattr(config, "RETRY_INITIAL_WAIT_S", 0.001)
    monkeypatch.setattr(config, "RETRY_MAX_WAIT_S", 0.002)


def _wrap(caller):
    return llm_retry()(type(caller).go)


# --- retry policy ----------------------------------------------------------------


def test_classification():
    assert is_transient(_rate_limit())
    assert is_transient(httpx.ConnectTimeout("t"))
    assert not is_transient(_bad_request())
    assert not is_transient(ValueError("bug"))
    err = RuntimeError("x")
    err.status_code = 502
    assert is_transient(err)


def test_transient_errors_are_retried_and_logged(fast_retry):
    c = _Caller([_rate_limit(), _rate_limit()])
    assert _wrap(c)(c) == "ok"
    assert c.calls == 3
    assert [f["stage"] for f in c.metrics.failures] == ["retry", "retry"]
    assert c.metrics.failures[0]["meta"]["attempt"] == 1


def test_non_transient_errors_fail_immediately(fast_retry):
    c = _Caller([_bad_request()])
    with pytest.raises(openai.BadRequestError):
        _wrap(c)(c)
    assert c.calls == 1 and c.metrics.failures == []


def test_programming_errors_are_not_retried(fast_retry):
    c = _Caller([ValueError("bug")])
    with pytest.raises(ValueError):
        _wrap(c)(c)
    assert c.calls == 1


def test_schema_mismatch_is_reasked_once(fast_retry):
    c = _Caller([StructuredOutputError("a"), StructuredOutputError("b"), StructuredOutputError("c")])
    with pytest.raises(StructuredOutputError):
        _wrap(c)(c)
    assert c.calls == config.RETRY_MAX_PARSE_ATTEMPTS == 2


def test_transient_errors_stop_at_the_limit_and_reraise_the_original(fast_retry):
    c = _Caller([_rate_limit() for _ in range(20)])
    with pytest.raises(openai.RateLimitError):
        _wrap(c)(c)
    assert c.calls == config.RETRY_MAX_ATTEMPTS


# --- cache ------------------------------------------------------------------------


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "CACHE_ENABLED", True)
    return tmp_path / "cache"


def test_cache_roundtrip(cache_dir):
    key = cache.make_key("k", "a", 1)
    assert cache.get("k", key) is None
    cache.put("k", key, {"x": [1, 2]})
    assert cache.get("k", key) == {"x": [1, 2]}


def test_cache_can_be_disabled(cache_dir, monkeypatch):
    key = cache.make_key("k", "a")
    cache.put("k", key, {"x": 1})
    monkeypatch.setattr(config, "CACHE_ENABLED", False)
    assert cache.get("k", key) is None
    cache.put("k", cache.make_key("k", "b"), {"x": 2})
    monkeypatch.setattr(config, "CACHE_ENABLED", True)
    assert cache.get("k", cache.make_key("k", "b")) is None  # the disabled put wrote nothing


def test_corrupt_entry_is_a_miss_not_an_error(cache_dir):
    key = cache.make_key("k", "a")
    cache.put("k", key, {"x": 1})
    (cache_dir / "k" / f"{key}.json").write_text("{not json", encoding="utf-8")
    assert cache.get("k", key) is None


def test_key_changes_with_every_input(tmp_path):
    img_a, img_b = tmp_path / "a.png", tmp_path / "b.png"
    img_a.write_bytes(b"one")
    img_b.write_bytes(b"two")
    base = cache.make_key("s", "m", "prompt", "text", files=[img_a])
    assert base == cache.make_key("s", "m", "prompt", "text", files=[img_a])
    assert base != cache.make_key("s", "m", "prompt2", "text", files=[img_a])
    assert base != cache.make_key("s", "m", "prompt", "text2", files=[img_a])
    assert base != cache.make_key("s", "m", "prompt", "text", files=[img_b])
    assert base != cache.make_key("t", "m", "prompt", "text", files=[img_a])


class _Out(BaseModel):
    answer: str


class _FakeChat:
    invocations = 0

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages):
        type(self).invocations += 1
        return {
            "parsed": _Out(answer="42"),
            "raw": SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 10}),
            "parsing_error": None,
        }


def test_structured_chat_second_identical_call_is_free(cache_dir, monkeypatch):
    monkeypatch.setattr(client_mod, "_load_client", lambda: object())
    monkeypatch.setattr(client_mod, "ChatOpenAI", _FakeChat)
    _FakeChat.invocations = 0
    c = LLMClient(metrics=Metrics())

    first = c.structured_chat("sys", "user", _Out, stage="writer")
    cost_after_first = c.metrics.cost_usd
    second = c.structured_chat("sys", "user", _Out, stage="writer")

    assert first == second == _Out(answer="42")
    assert _FakeChat.invocations == 1  # the second call never reached the model
    assert c.metrics.cost_usd == cost_after_first  # and cost nothing
    assert c.metrics.records[-1]["meta"] == {"cached": True}

    c.structured_chat("sys", "different user text", _Out, stage="writer")
    assert _FakeChat.invocations == 2  # any change to the input is a miss


def test_chat_model_is_built_with_pinned_temperature_and_no_internal_retries(cache_dir, monkeypatch):
    seen = {}

    class _Spy(_FakeChat):
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(client_mod, "_load_client", lambda: object())
    monkeypatch.setattr(client_mod, "ChatOpenAI", _Spy)
    LLMClient(metrics=Metrics()).structured_chat("sys", "u", _Out, stage="writer")
    assert seen["temperature"] == config.LLM_TEMPERATURE == 0.0
    assert seen["max_retries"] == 0
