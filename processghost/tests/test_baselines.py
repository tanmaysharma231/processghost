"""The single-model baseline and the method comparison, with a fake client and hand-written traces:
no API call, no real recording. (Running them for real costs money and is a separate, measured step.)"""
from __future__ import annotations

import json

import cv2

from core.models.tokens import TranscriptSegment
from evals.baseline_single_model import (
    BASELINE_SYSTEM, BaselineFinding, BaselineOutput, evenly_spaced_times, grab_frames, render_baseline_md,
    run_single_model,
)
from evals.compare_methods import format_table, method_row, summarize_trace
from evals.score_findings import score_text
from tests.fakes import _FakeClient
from tests.test_screen_timeline import _white, _with_rects, _write_video

SPEC = {
    "findings": [
        {"id": "F1", "name": "lookup key", "groups": [["lookup", "key"]]},
        {"id": "F2", "name": "status value", "groups": [["status", "paid"]]},
    ],
    "forbidden": [{"pattern": "invented claim"}],
    "completeness_banners": ["Nothing is missing"],
}


def test_evenly_spaced_times_are_slice_centres():
    assert evenly_spaced_times(12.0, 4) == [1.5, 4.5, 7.5, 10.5]
    assert evenly_spaced_times(0.0, 4) == [] and evenly_spaced_times(10.0, 0) == []


def test_grab_frames_writes_one_png_per_time(tmp_path):
    video = _write_video(tmp_path / "v.mp4", [(2.0, _white), (2.0, lambda: _with_rects((100, 60, 60, 30)))])
    paths = grab_frames(video, [0.5, 3.0], tmp_path / "frames")
    assert len(paths) == 2 and all(p.exists() for p in paths)
    assert cv2.imread(str(paths[0])).mean() > cv2.imread(str(paths[1])).mean()  # the rectangle appears later


def test_baseline_makes_one_call_with_the_requested_frame_budget(tmp_path):
    video = _write_video(tmp_path / "v.mp4", [(4.0, _white)])
    answer = BaselineOutput(findings=[BaselineFinding(kind="unresolved_requirement", statement="The lookup key is not stated.", at_s=2.0)])
    client = _FakeClient(structured_response=answer)
    transcript = [TranscriptSegment(start_s=0.0, end_s=3.0, text="match the rows here")]
    out = run_single_model(client, video, transcript, 3, tmp_path / "out")
    assert out is answer and len(client.structured_calls) == 1
    system, user, _schema, images = client.structured_calls[0]
    assert system == BASELINE_SYSTEM and len(images) == 3
    assert "match the rows here" in user and "image 1: frame at t=" in user
    assert (tmp_path / "out" / "sop.md").exists()


def test_baseline_markdown_is_scored_by_the_existing_scorer_one_finding_per_line():
    out = BaselineOutput(
        findings=[
            BaselineFinding(kind="unresolved_requirement", statement="The lookup\nkey is not stated.", evidence_quote="match here"),
            BaselineFinding(kind="observed_unexplained", statement="A cell changed with no explanation."),
        ]
    )
    md = render_baseline_md(out, frames=12)
    score = score_text(md, SPEC)
    assert [f["surfaced"] for f in score["findings"]] == [True, False]
    assert "12 evenly spaced frames" in md and "Nothing is missing" not in md


def _write_trace(run_dir, calls):
    run_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"event": "call", "stage": s, "usage": {"prompt_tokens": tin, "completion_tokens": tout}, "cost_usd": c}) for s, tin, tout, c in calls]
    lines.append(json.dumps({"event": "payload", "stage": "checker", "payload": {}}))  # not a call: ignored
    (run_dir / "trace.jsonl").write_text("\n".join(lines), encoding="utf-8")


def test_trace_summary_counts_calls_tokens_and_cost_per_stage(tmp_path):
    _write_trace(tmp_path / "r", [("writer", 1000, 200, 0.01), ("checker", 5000, 300, 0.05), ("checker", 4000, 300, 0.04)])
    s = summarize_trace(tmp_path / "r")
    assert s["calls"] == 3 and s["tokens_in"] == 10000 and abs(s["cost_usd"] - 0.10) < 1e-9
    assert s["by_stage"]["checker"]["calls"] == 2


def test_missing_trace_is_an_empty_summary_not_an_error(tmp_path):
    assert summarize_trace(tmp_path)["calls"] == 0


def _run(tmp_path, name, text, calls):
    d = tmp_path / name
    _write_trace(d, calls)
    (d / "sop.md").write_text(text, encoding="utf-8")
    return d


def test_method_rows_and_table_report_measurements_and_a_poc_warning(tmp_path):
    a = _run(tmp_path, "a", "- the lookup key is unclear\n- status is paid\n", [("checker", 8000, 100, 0.08)])
    b = _run(tmp_path, "b", "- the lookup key\ninvented claim\nNothing is missing\n", [("checker", 1000, 100, 0.01)])
    row_a, row_b = method_row("adaptive", [a], SPEC), method_row("v2", [b], SPEC)
    assert row_a["surfaced_mean"] == 2 and row_a["forbidden_runs"] == 0 and row_a["calls_mean"] == 1
    assert row_b["surfaced_mean"] == 1 and row_b["forbidden_runs"] == 1 and row_b["false_completeness_runs"] == 1
    table = format_table([row_a, row_b])
    assert "adaptive" in table and "v2" in table and "POC only" in table


def test_findings_per_dollar_is_not_computed_for_a_free_cached_run(tmp_path):
    d = _run(tmp_path, "c", "- the lookup key\n", [("writer", 0, 0, 0.0)])
    assert "findings per $: n/a" in format_table([method_row("cached", [d], SPEC)])
