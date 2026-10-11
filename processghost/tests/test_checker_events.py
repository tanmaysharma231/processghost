"""Unit tests for the v3 Checker path: event selection, evidence assembly, the deterministic
two-level escalation, the echo downgrade and the failure contract. Synthetic keyframes on disk and a
fake client -- no video, no OCR engine, no API."""
from __future__ import annotations

from pathlib import Path
from typing import List

import cv2
import numpy as np
import pytest

from core import config
from core.io.video import frame_name_for
from core.models.screen import ScreenEvent, ScreenTimeline
from core.models.sop import Attribution, CheckerOutput, Flag, SOPDocument, SOPStep
from core.models.tokens import TranscriptSegment
from core.pipeline import _check_flag_with_events, _downgrade_echo
from core.screen.evidence import build_evidence, select_events
from tests.fakes import _FakeClient

W, H = 640, 360


def _write_keyframe(path: Path, tone: int, rect=None) -> None:
    img = np.full((H, W, 3), tone, dtype=np.uint8)
    if rect:
        x, y, w, h = rect
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 0), -1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)


def _event(tdir: Path, n: int, t: float, typ: str = "local") -> ScreenEvent:
    before, after = f"keyframes/{frame_name_for(t - 0.3)}", f"keyframes/{frame_name_for(t + 0.3)}"
    _write_keyframe(tdir / before, 255)
    _write_keyframe(tdir / after, 255, rect=(100, 80, 80, 30))
    return ScreenEvent(
        event_id=f"ev-{n:03d}", t_start=t, t_end=t + 0.3, type=typ, changed_pct=1.0, coverage_pct=10.0,
        bboxes=[(100, 80, 80, 30)], before_frame=before, after_frame=after,
    )


def _timeline(tdir: Path, times: List[float]) -> ScreenTimeline:
    return ScreenTimeline(
        video="v.mp4", duration_s=60.0, width=W, height=H, events=[_event(tdir, i + 1, t) for i, t in enumerate(times)]
    )


def _doc(flag: Flag) -> SOPDocument:
    steps = [
        SOPStep(step_id="step-01", order=1, title="Open it", instruction="Open it.",
                attributions=[Attribution(kind="said", t_s=0.0, segment_ids=[0])]),
        SOPStep(step_id="step-02", order=2, title="Paste it here", instruction="Paste it here.",
                attributions=[Attribution(kind="said", t_s=12.0, segment_ids=[1])]),
    ]
    return SOPDocument(run_id="r", steps=steps, flags=[flag])


TRANSCRIPT = [
    TranscriptSegment(start_s=0.0, end_s=6.0, text="open it"),
    TranscriptSegment(start_s=12.0, end_s=18.0, text="paste it here"),
]


def _flag(event_ids) -> Flag:
    return Flag(flag_id="FLAG-01", family="ambiguity", question="Where does it go?", severity="blocking",
                window_segment_ids=[1], step_id="step-02", event_ids=event_ids)


def _out(verdict, observed="Cell F18 changed from empty to 45,000.", inferred="cell F18 in the Actual column", reason=""):
    return CheckerOutput(observed=observed, verdict=verdict,
                         inferred_text=inferred if verdict == "resolved" else None, reason=reason)


@pytest.fixture(autouse=True)
def _openai(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")


# --- event selection ----------------------------------------------------------


def test_level0_picks_the_nearest_attached_event(tmp_path):
    tl = _timeline(tmp_path, [2.0, 13.0, 30.0])
    picked = select_events(["ev-001", "ev-002", "ev-003"], tl.events, 14.0, level=0)
    assert [e.event_id for e in picked] == ["ev-002"]


def test_level1_widens_by_the_configured_number_of_events(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SCREEN_ESCALATION_EVENTS", 1)
    tl = _timeline(tmp_path, [2.0, 8.0, 13.0, 20.0, 30.0])
    picked = select_events(["ev-003"], tl.events, 13.0, level=1)
    assert [e.event_id for e in picked] == ["ev-002", "ev-003", "ev-004"]


def test_no_attached_events_selects_nothing(tmp_path):
    assert select_events([], _timeline(tmp_path, [2.0]).events, 2.0, 0) == []


# --- evidence assembly ----------------------------------------------------------


def test_evidence_has_full_frame_and_tiles_and_ocr(tmp_path):
    tl = _timeline(tmp_path, [13.0])
    pack = build_evidence(tl.events, tmp_path, tmp_path / "ev", ocr=lambda img: "TEXT")
    names = [p.name for p in pack.images]
    assert names == ["ev-001_full.png", "ev-001_tiles.png"]
    assert cv2.imread(str(pack.images[0])).shape[1] <= config.SCREEN_EVIDENCE_FULL_WIDTH
    assert "screen chrome" in pack.ocr_text and "changed region 1" in pack.ocr_text
    assert pack.after_frames == [tmp_path / tl.events[0].after_frame]
    assert "numbered tiles" in pack.description


def test_scene_cut_adds_a_low_res_before_frame(tmp_path):
    tl = ScreenTimeline(video="v", duration_s=1, width=W, height=H, events=[_event(tmp_path, 1, 5.0, "scene_cut")])
    pack = build_evidence(tl.events, tmp_path, tmp_path / "ev")
    assert any(p.name.endswith("_before.png") for p in pack.images)
    assert pack.ocr_text == ""  # no OCR engine given


def test_image_count_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SCREEN_EVIDENCE_MAX_IMAGES", 3)
    tl = _timeline(tmp_path, [1.0, 2.0, 3.0, 4.0])
    assert len(build_evidence(tl.events, tmp_path, tmp_path / "ev").images) == 3


# --- the two-level Checker ------------------------------------------------------


def _run(tmp_path, responses, event_times=(13.0, 20.0, 30.0), flag_events=("ev-001", "ev-002", "ev-003"), ocr=lambda i: "x"):
    tl = _timeline(tmp_path, list(event_times))
    flag = _flag(list(flag_events))
    client = _FakeClient(structured_response=responses)
    doc = _check_flag_with_events(client, _doc(flag), flag, TRANSCRIPT, tl, tmp_path, tmp_path / "ev", ocr)
    return client, doc


def test_resolved_at_level0_makes_one_text_only_call(tmp_path):
    client, doc = _run(tmp_path, [_out("resolved")])
    assert len(client.structured_calls) == 1
    assert client.structured_calls[0][3] is None  # no images at level 0
    f = doc.flags[0]
    assert f.status == "resolved" and f.checker_tier == "ocr" and f.expansion_level == 0


def test_unresolvable_escalates_once_with_images(tmp_path):
    client, doc = _run(tmp_path, [_out("unresolvable", reason="not shown"), _out("resolved")])
    assert len(client.structured_calls) == 2
    assert client.structured_calls[1][3]  # level 1 carries images (OpenAI path)
    f = doc.flags[0]
    assert f.status == "resolved" and f.checker_tier == "vision" and f.expansion_level == 1


def test_two_unresolvable_calls_stop_at_two(tmp_path):
    client, doc = _run(tmp_path, [_out("unresolvable", reason="no")])
    assert len(client.structured_calls) == 2
    assert doc.flags[0].status == "unresolvable" and doc.flags[0].expansion_level == 1


def test_sarvam_level1_stays_text_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "sarvam")
    client, doc = _run(tmp_path, [_out("unresolvable", reason="no"), _out("resolved")])
    assert client.structured_calls[1][3] is None
    assert doc.flags[0].checker_tier == "ocr"


def test_echoed_observation_is_downgraded_then_escalated(tmp_path):
    echo = _out("resolved", observed="Where does it go? Paste it here.")
    client, doc = _run(tmp_path, [echo, _out("resolved")])
    assert len(client.structured_calls) == 2  # the downgrade triggered level 1
    assert doc.flags[0].status == "resolved" and doc.flags[0].expansion_level == 1


def test_no_events_marks_the_flag_check_failed(tmp_path):
    client, doc = _run(tmp_path, [_out("resolved")], flag_events=())
    assert doc.flags[0].status == "check_failed" and client.structured_calls == []


def test_call_exception_marks_check_failed_and_records_failure(tmp_path):
    client, doc = _run(tmp_path, [RuntimeError("rate limited")])
    assert doc.flags[0].status == "check_failed"
    assert client.metrics.failures and client.metrics.failures[0]["stage"] == "checker"


def test_downgrade_only_touches_resolved_verdicts():
    unresolved = _out("unresolvable", observed="Where does it go?", reason="x")
    assert _downgrade_echo(unresolved, "Where does it go?") is unresolved
    fresh = _out("resolved", observed="Cell F18 shows 45,000 in the Actual column.")
    assert _downgrade_echo(fresh, "Where does it go? Paste it here.").verdict == "resolved"
