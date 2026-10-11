"""Unit tests for input validation (core/guards.py), the per-run budget, data retention
(core/retention.py), the provider timeout, and the no-secrets-in-outputs check. No network."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core import config
from core import guards
from core.guards import InputRejected, validate_input
from core.llm import client as client_mod
from core.llm.client import LLMClient, Metrics
from core.manifest import build_manifest
from core.models.sop import Attribution, Flag, SOPDocument, SOPStep
from core.models.tokens import TranscriptSegment
from core.pipeline import _run_checker
from core.retention import apply_cleanup, plan_cleanup, strip_media
from tests.fakes import _FakeClient


# --- input validation ----------------------------------------------------------


def _video(tmp_path, size=10) -> Path:
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x" * size)
    return p


def test_valid_input_returns_its_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(guards, "probe_duration_s", lambda p: 42.0)
    assert validate_input(_video(tmp_path)) == 42.0


def test_missing_and_empty_files_are_rejected(tmp_path):
    with pytest.raises(InputRejected, match="not a file"):
        validate_input(tmp_path / "nope.mp4")
    with pytest.raises(InputRejected, match="empty"):
        validate_input(_video(tmp_path, size=0))


def test_oversized_file_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_VIDEO_BYTES", 5)
    with pytest.raises(InputRejected, match="MAX_VIDEO_BYTES"):
        validate_input(_video(tmp_path, size=10))


def test_unreadable_duration_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(guards, "probe_duration_s", lambda p: None)
    with pytest.raises(InputRejected, match="duration"):
        validate_input(_video(tmp_path))


def test_over_long_video_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(guards, "probe_duration_s", lambda p: 5000.0)
    with pytest.raises(InputRejected, match="MAX_VIDEO_DURATION_S"):
        validate_input(_video(tmp_path))


# --- per-run budget --------------------------------------------------------------


def test_over_budget_follows_the_limit(monkeypatch):
    m = Metrics()
    m.cost_usd = 1.0
    monkeypatch.setattr(config, "MAX_RUN_COST_USD", 2.0)
    assert not m.over_budget()
    m.cost_usd = 2.0
    assert m.over_budget()
    monkeypatch.setattr(config, "MAX_RUN_COST_USD", 0)
    assert not m.over_budget()  # 0 disables the limit


def _doc_with_flags(n) -> SOPDocument:
    step = SOPStep(step_id="step-01", order=1, title="t", instruction="i",
                   attributions=[Attribution(kind="said", t_s=0.0, segment_ids=[0])])
    flags = [Flag(flag_id=f"FLAG-{i:02d}", family="ambiguity", question="q", severity="blocking",
                  window_segment_ids=[0], step_id="step-01") for i in range(1, n + 1)]
    return SOPDocument(run_id="r", steps=[step], flags=flags)


def test_checker_stops_starting_flags_once_over_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_RUN_COST_USD", 1.0)
    client = _FakeClient()
    client.metrics.cost_usd = 5.0  # already over budget before the Checker starts
    doc = _run_checker(client, _doc_with_flags(3), [TranscriptSegment(start_s=0.0, end_s=5.0, text="x")],
                       tmp_path / "v.mp4", tmp_path / "frames")
    assert {f.status for f in doc.flags} == {"unchecked"}  # never silently dropped
    assert client.structured_calls == []
    assert any(f["stage"] == "budget_exceeded" and f["meta"]["flags_not_checked"] == 3 for f in client.metrics.failures)


# --- retention --------------------------------------------------------------------


def _make_run(root: Path, name: str, age_days: float) -> Path:
    run = root / name
    (run / "frames").mkdir(parents=True)
    (run / "frames" / "a.png").write_bytes(b"x")
    (run / "video.mp4").write_bytes(b"v")
    (run / "audio.wav").write_bytes(b"a")
    (run / "sop.md").write_text("kept", encoding="utf-8")
    old = time.time() - age_days * 86400
    os.utime(run, (old, old))
    return run


def test_strip_media_keeps_text_outputs(tmp_path):
    run = _make_run(tmp_path, "r1", 0)
    removed = strip_media(run)
    assert len(removed) == 3
    assert (run / "sop.md").read_text(encoding="utf-8") == "kept"
    assert not (run / "video.mp4").exists() and not (run / "frames").exists()


def test_plan_lists_only_old_runs_and_deletes_nothing(tmp_path):
    old, new = _make_run(tmp_path, "old", 40), _make_run(tmp_path, "new", 1)
    plan = plan_cleanup(tmp_path, None, older_than_days=30)
    assert [a.path for a in plan] == [old]
    assert new.exists() and old.exists()  # planning never deletes


def test_apply_removes_whole_old_runs(tmp_path):
    old, new = _make_run(tmp_path, "old", 40), _make_run(tmp_path, "new", 1)
    assert apply_cleanup(plan_cleanup(tmp_path, None, 30)) == 1
    assert not old.exists() and new.exists()


def test_media_only_keeps_the_run_directory(tmp_path):
    old = _make_run(tmp_path, "old", 40)
    plan = plan_cleanup(tmp_path, None, 30, media_only=True)
    assert plan[0].kind == "media"
    apply_cleanup(plan)
    assert old.exists() and (old / "sop.md").exists() and not (old / "video.mp4").exists()


def test_cache_entries_are_cleaned_only_when_asked(tmp_path):
    cache = tmp_path / "cache"
    (cache / "structured").mkdir(parents=True)
    f = cache / "structured" / "k.json"
    f.write_text("{}", encoding="utf-8")
    old = time.time() - 40 * 86400
    os.utime(f, (old, old))
    assert plan_cleanup(tmp_path / "runs", cache, 30) == []
    plan = plan_cleanup(tmp_path / "runs", cache, 30, include_cache=True)
    assert [a.kind for a in plan] == ["cache"]
    apply_cleanup(plan)
    assert not f.exists()


# --- timeouts and secrets ------------------------------------------------------------


def test_provider_calls_have_a_timeout(monkeypatch):
    seen = {}

    class _Spy:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def with_structured_output(self, *a, **k):
            raise RuntimeError("stop after construction")

    monkeypatch.setattr(client_mod, "_load_client", lambda: object())
    monkeypatch.setattr(client_mod, "ChatOpenAI", _Spy)
    from pydantic import BaseModel

    class Out(BaseModel):
        x: str

    with pytest.raises(RuntimeError, match="stop after construction"):
        LLMClient(metrics=Metrics()).structured_chat("s", "u", Out, stage="writer")
    assert seen["timeout"] == config.LLM_TIMEOUT_S > 0


def test_manifest_never_contains_api_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value-123")
    monkeypatch.setenv("SARVAM_API_KEY", "sarvam-secret-value-456")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"abc")
    text = str(build_manifest(video))
    assert "sk-test-secret-value-123" not in text and "sarvam-secret-value-456" not in text
