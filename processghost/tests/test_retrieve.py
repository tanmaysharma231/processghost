"""Retrieval library (ADR 0012): stable ids, caching, budget and refusals, on a synthetic video."""
from __future__ import annotations

import cv2
import pytest

from core.models.screen import EvidenceRef, Refusal
from core.models.tokens import TranscriptSegment
from core.screen.retrieve import InspectionBudget, Retriever
from core.screen.timeline import build_screen_timeline
from tests.test_screen_timeline import _no_ocr, _white, _with_rects, _write_video


@pytest.fixture()
def clip(tmp_path):
    video = _write_video(tmp_path / "v.mp4", [(2.0, _white), (3.0, lambda: _with_rects((100, 60, 60, 30)))])
    timeline = build_screen_timeline(video, tmp_path / "tl", ocr=_no_ocr)
    return video, timeline, tmp_path


def _retriever(clip, **kw) -> Retriever:
    video, timeline, tmp = clip
    return Retriever(video, tmp / "ev", timeline=timeline, timeline_dir=tmp / "tl", **kw)


def test_context_frame_has_a_stable_id_and_a_file(clip):
    r = _retriever(clip)
    ref = r.get_context_frame(3.0)
    assert isinstance(ref, EvidenceRef) and ref.kind == "context_frame" and ref.t_s == 3.0
    assert (clip[2] / "ev" / ref.path).exists() and ref.video_sha == clip[1].video_sha
    assert r.get_context_frame(3.0).evidence_id == ref.evidence_id
    assert r.get_context_frame(4.0).evidence_id != ref.evidence_id


def test_ids_are_the_same_in_a_new_retriever_and_cost_nothing(clip):
    first = _retriever(clip)
    ref = first.get_context_frame(3.0)
    second = _retriever(clip)
    again = second.get_context_frame(3.0)
    assert again.evidence_id == ref.evidence_id
    assert second.decodes == 0 and second.budget.used["images"] == 0  # found on disk: no decode, no charge


def test_repeated_calls_do_not_decode_or_charge_twice(clip):
    r = _retriever(clip)
    r.get_context_frame(3.0)
    decodes, used = r.decodes, dict(r.budget.used)
    r.get_context_frame(3.0)
    assert r.decodes == decodes and r.budget.used == used


def test_crop_keeps_coordinates_and_is_full_resolution(clip):
    r = _retriever(clip)
    ref = r.get_crop(3.0, (100, 60, 60, 30), pad=10)
    assert ref.box == (100, 60, 60, 30)
    img = cv2.imread(str(clip[2] / "ev" / ref.path))
    assert img.shape[:2] == (50, 80)  # 30+2*10 high, 60+2*10 wide: no resampling
    assert r.budget.used["crops"] == 1


def test_compare_frames_stacks_before_over_after(clip):
    r = _retriever(clip)
    ref = r.compare_frames(1.0, 4.0, box=(100, 60, 60, 30))
    img = cv2.imread(str(clip[2] / "ev" / ref.path))
    half = img.shape[0] // 2
    assert img[:half].mean() > img[half:].mean()  # white above, the black rectangle's crop below
    assert (ref.t_s, ref.t_end) == (1.0, 4.0)


def test_changed_regions_find_the_edit_and_are_cached(clip):
    r = _retriever(clip)
    res = r.get_changed_regions(1.0, 4.0)
    assert len(res.boxes) == 1 and abs(res.boxes[0][0] - 100) <= 12 and res.groups
    decodes = r.decodes
    assert r.get_changed_regions(1.0, 4.0).evidence_id == res.evidence_id and r.decodes == decodes
    with_frames = r.get_changed_regions(1.0, 4.0, include_frames=True)
    assert [f.kind for f in with_frames.frames] == ["context_frame", "context_frame"]


def test_spent_budget_returns_a_refusal_but_cached_evidence_is_still_served(clip):
    r = _retriever(clip, budget=InspectionBudget(images=1))
    first = r.get_context_frame(1.0)
    refused = r.get_context_frame(4.0)
    assert isinstance(first, EvidenceRef) and isinstance(refused, Refusal)
    assert "budget exhausted" in refused.reason and r.refusals == [refused]
    assert isinstance(r.get_context_frame(1.0), EvidenceRef)  # already handed out: free


def test_crop_budget_is_separate_from_image_budget(clip):
    r = _retriever(clip, budget=InspectionBudget(crops=0))
    assert isinstance(r.get_crop(3.0, (100, 60, 60, 30)), Refusal)
    assert isinstance(r.get_context_frame(3.0), EvidenceRef)


def test_inspect_episode_lists_events_and_spoken_text(clip):
    video, timeline, tmp = clip
    segs = [TranscriptSegment(start_s=0.0, end_s=3.0, text="first"), TranscriptSegment(start_s=10.0, end_s=12.0, text="later")]
    r = Retriever(video, tmp / "ev", timeline=timeline, timeline_dir=tmp / "tl", transcript=segs)
    ep = r.inspect_episode(0.0, 4.0)
    assert ep.event_ids == ["ev-001"] and ep.transcript == ["first"] and ep.frames == []
    deeper = r.inspect_episode(0.0, 4.0, detail="keyframes")
    assert [f.kind for f in deeper.frames] == ["keyframe", "keyframe"] and r.budget.used["images"] == 2


def test_reinspecting_one_interval_is_limited(clip):
    r = _retriever(clip, budget=InspectionBudget(retries_per_episode=1))
    assert not isinstance(r.inspect_episode(0.0, 4.0), Refusal)
    assert not isinstance(r.inspect_episode(0.0, 4.0, detail="keyframes"), Refusal)
    r.budget.inspections["0.00-3.50"] = 5  # an interval already inspected too many times
    assert isinstance(r.inspect_episode(0.0, 3.5), Refusal)
    assert not isinstance(r.inspect_episode(0.0, 4.0, detail="keyframes"), Refusal)  # cached: still served
    assert not isinstance(r.inspect_episode(0.0, 3.0), Refusal)  # a different interval has its own count


def test_expand_interval_is_clipped_and_budgeted(clip):
    r = _retriever(clip, budget=InspectionBudget(expansions=1))
    wide = r.expand_interval(1.0, 2.0, lead=5.0, lag=100.0)
    assert wide.t_start == 0.0 and wide.t_end == pytest.approx(clip[1].duration_s, abs=0.1)
    assert isinstance(r.expand_interval(1.0, 2.0, lead=1.0, lag=1.0), Refusal)


def test_every_call_is_traced_with_cache_and_refusal_flags(clip):
    calls = []
    r = _retriever(clip, budget=InspectionBudget(images=1), on_call=calls.append)
    r.get_context_frame(1.0)
    r.get_context_frame(1.0)
    r.get_context_frame(4.0)
    assert [(c["tool"], c["cache_hit"], c["refused"]) for c in calls] == [
        ("get_context_frame", False, False), ("get_context_frame", True, False), ("get_context_frame", False, True),
    ]
    assert all(c["stage"] == "retrieve" and "latency_ms" in c for c in calls)


def test_different_video_gives_different_ids(clip, tmp_path):
    other = _write_video(tmp_path / "o.mp4", [(2.0, _white), (3.0, lambda: _with_rects((10, 10, 80, 80)))])
    a = _retriever(clip).get_context_frame(3.0)
    b = Retriever(other, tmp_path / "ev2").get_context_frame(3.0)
    assert a.evidence_id != b.evidence_id
