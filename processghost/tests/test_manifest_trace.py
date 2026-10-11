"""Unit tests for core/manifest.py and Metrics' run_id / stage events (core/llm/client.py)."""
from __future__ import annotations

import json

import pytest

from core.llm.client import Metrics
from core.manifest import build_manifest, prompt_hashes
from core.models.sop import SCHEMA_VERSION, SOPDocument
from report.build_spec import build_spec


def test_manifest_describes_the_input_and_the_code(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"abc")
    m = build_manifest(video, provider="openai")
    assert m["provider"] == "openai"
    assert m["input"]["name"] == "v.mp4" and m["input"]["bytes"] == 3
    assert m["input"]["sha256"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert {"WRITER_SYSTEM", "REVIEWER_SYSTEM", "CHECKER_SYSTEM", "CHECKER_EVENT_SYSTEM"} <= set(m["prompt_hashes"])
    assert m["config"]["LLM_TEMPERATURE"] == 0.0 and "SCREEN_FPS" in m["config"]
    assert "git_commit" in m and "packages" in m
    json.dumps(m)  # must be serializable as-is


def test_prompt_hash_changes_when_a_prompt_changes(monkeypatch):
    from core.llm import prompts

    before = prompt_hashes()["WRITER_SYSTEM"]
    monkeypatch.setattr(prompts, "WRITER_SYSTEM", prompts.WRITER_SYSTEM + " ")
    assert prompt_hashes()["WRITER_SYSTEM"] != before


def test_trace_lines_carry_run_id(tmp_path):
    m = Metrics(trace_path=tmp_path / "t.jsonl")
    m.run_id = "run-1"
    m.record_failure("x", "boom")
    line = json.loads((tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["run_id"] == "run-1" and line["stage"] == "x"


def test_stage_brackets_record_start_end_and_duration(tmp_path):
    m = Metrics(trace_path=tmp_path / "t.jsonl")
    with m.stage("writer"):
        pass
    start, end = [json.loads(l) for l in (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()]
    assert start["event"] == "stage_start" and end["event"] == "stage_end"
    assert end["ok"] is True and end["duration_s"] >= 0


def test_stage_records_ok_false_when_the_stage_raises(tmp_path):
    m = Metrics(trace_path=tmp_path / "t.jsonl")
    with pytest.raises(ValueError):
        with m.stage("writer"):
            raise ValueError("x")
    end = json.loads((tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert end["ok"] is False


def test_outputs_carry_a_schema_version():
    doc = SOPDocument(run_id="r")
    assert doc.schema_version == SCHEMA_VERSION
    assert build_spec(doc)["schema_version"] == SCHEMA_VERSION
