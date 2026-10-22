"""Unit tests for core/llm/client.py's cost estimation and trace logging -- no API key or network
call required.
"""
from __future__ import annotations

import json

from core.llm.client import Metrics, estimate_cost_usd


# --- estimate_cost_usd -------------------------------------------------------

def test_estimate_cost_usd_known_model_matches_hand_computed_value():
    # gpt-4o: $2.50/1M prompt, $10.00/1M completion (core/config.py: PRICING_PER_1M_TOKENS)
    cost, known = estimate_cost_usd("gpt-4o", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert known is True
    assert cost == 12.50


def test_estimate_cost_usd_zero_tokens_is_zero_cost():
    cost, known = estimate_cost_usd("gpt-4o", prompt_tokens=0, completion_tokens=0)
    assert known is True
    assert cost == 0.0


def test_estimate_cost_usd_unknown_model_returns_zero_and_not_known():
    cost, known = estimate_cost_usd("some-future-model", prompt_tokens=1000, completion_tokens=1000)
    assert cost == 0.0
    assert known is False


# --- Metrics.add ---------------------------------------------------------------

def test_add_accumulates_tokens_and_cost():
    m = Metrics()
    m.add({"prompt_tokens": 100, "completion_tokens": 50}, "gpt-4o-mini", stage="writer")
    assert m.tokens_in == 100
    assert m.tokens_out == 50
    assert m.cost_usd > 0.0
    assert len(m.records) == 1
    assert m.records[0]["stage"] == "writer"
    assert m.records[0]["model"] == "gpt-4o-mini"


def test_add_unknown_model_marks_pricing_unknown_in_meta():
    m = Metrics()
    m.add({"prompt_tokens": 100, "completion_tokens": 50}, "mystery-model", stage="checker")
    assert m.records[0]["cost_usd"] == 0.0
    assert m.records[0]["meta"]["pricing"] == "unknown"


def test_add_cost_usd_override_used_verbatim_and_not_recomputed():
    m = Metrics()
    m.add({}, "whisper-1", stage="transcribe", cost_usd_override=0.42)
    assert m.cost_usd == 0.42
    assert m.records[0]["cost_usd"] == 0.42
    assert "pricing" not in m.records[0].get("meta", {})


def test_add_records_latency_when_given():
    m = Metrics()
    m.add({}, "gpt-4o", stage="writer", latency_ms=123.4)
    assert m.records[0]["latency_ms"] == 123.4


# --- trace.jsonl -----------------------------------------------------------

def test_add_appends_trace_line_when_trace_path_set(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    m = Metrics(trace_path=trace_path)
    m.add({"prompt_tokens": 10, "completion_tokens": 5}, "gpt-4o", stage="writer")

    lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "call"
    assert event["stage"] == "writer"
    assert event["model"] == "gpt-4o"


def test_record_failure_appends_trace_line_when_trace_path_set(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    m = Metrics(trace_path=trace_path)
    m.record_failure("checker", "rate limited", meta={"flag_id": "FLAG-01"})

    lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "failure"
    assert event["stage"] == "checker"
    assert event["error"] == "rate limited"


def test_record_payload_appends_trace_line_with_the_real_content(tmp_path):
    """This is the mechanism a live progress view reads -- see docs/18-known-gaps-v2.md. Unlike
    add()'s "call" event (metadata only), a "payload" event carries the actual structured output
    a Writer/Reviewer/Checker call produced."""
    trace_path = tmp_path / "trace.jsonl"
    m = Metrics(trace_path=trace_path)
    m.record_payload("checker", {"verdict": "resolved", "inferred_text": "Column F"})

    lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "payload"
    assert event["stage"] == "checker"
    assert event["payload"] == {"verdict": "resolved", "inferred_text": "Column F"}


def test_record_payload_does_not_affect_cost_or_token_accounting():
    """A payload line must never be mistaken for a billable call -- add() is the only thing that
    touches tokens_in/tokens_out/cost_usd."""
    m = Metrics()
    m.record_payload("writer", {"steps": []})
    assert m.tokens_in == 0
    assert m.tokens_out == 0
    assert m.cost_usd == 0.0
    assert m.records == []


def test_multiple_calls_append_multiple_lines_in_order(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    m = Metrics(trace_path=trace_path)
    m.add({"prompt_tokens": 1, "completion_tokens": 1}, "gpt-4o", stage="writer")
    m.record_failure("reviewer", "boom")
    m.add({"prompt_tokens": 2, "completion_tokens": 2}, "gpt-4o-mini", stage="checker")

    lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    stages = [json.loads(line)["stage"] for line in lines]
    assert stages == ["writer", "reviewer", "checker"]


def test_no_trace_file_written_when_trace_path_is_none(tmp_path, monkeypatch):
    """Default behavior (trace_path=None) must not write anywhere -- back-compat for any caller
    that constructs Metrics() without one. The working directory is the empty tmp dir, so a stray
    relative-path write by any of these calls would show up in it."""
    monkeypatch.chdir(tmp_path)
    m = Metrics()
    m.add({"prompt_tokens": 1, "completion_tokens": 1}, "gpt-4o", stage="writer")
    m.record_failure("writer", "boom")
    m.record_payload("writer", {"steps": []})
    assert list(tmp_path.iterdir()) == []


def test_to_dict_unaffected_by_trace_path():
    m = Metrics()
    m.add({"prompt_tokens": 10, "completion_tokens": 5}, "gpt-4o", stage="writer")
    d = m.to_dict()
    assert d["tokens_in"] == 10
    assert d["tokens_out"] == 5
    assert d["cost_usd_estimate"] > 0.0
    assert len(d["calls"]) == 1
