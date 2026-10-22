"""Unit tests for core/screen/diff.py and core/screen/timeline.py on synthetic videos -- no API calls,
no real recordings. Synthetic screens are flat colours plus a few shapes, so every expected event
is known by construction."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Tuple

import cv2
import numpy as np
import pytest

from core import config
from core.screen.diff import diff_boxes
from core.screen.timeline import build_screen_timeline

W, H, FPS = 320, 180, 12  # 12 native fps -> exactly every 4th frame sampled at SCREEN_FPS=3

Frame = np.ndarray
Segment = Tuple[float, Callable[[], Frame]]  # (duration_s, frame factory)


def _white() -> Frame:
    return np.full((H, W, 3), 255, dtype=np.uint8)


def _with_rects(*rects: Tuple[int, int, int, int]) -> Frame:
    img = _white()
    for x, y, w, h in rects:
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 0), -1)
    return img


def _write_video(path: Path, segments: List[Segment]) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    assert writer.isOpened(), "this OpenCV build cannot write mp4v"
    for duration_s, make in segments:
        frame = make()
        for _ in range(int(duration_s * FPS)):
            writer.write(frame)
    writer.release()
    return path


def _no_ocr(_img: Frame) -> str:
    return ""


def test_diff_identical_frames():
    res = diff_boxes(_white(), _white())
    assert res.identical and res.boxes == [] and res.changed_frac == 0.0


def test_diff_finds_box_in_original_coordinates():
    res = diff_boxes(_white(), _with_rects((100, 60, 60, 30)))
    assert len(res.boxes) == 1
    x, y, w, h = res.boxes[0]
    assert abs(x - 100) <= 12 and abs(y - 60) <= 12  # dilation grows the box slightly
    assert w >= 60 and h >= 30
    assert not res.identical


def test_diff_mask_hides_region():
    res = diff_boxes(_white(), _with_rects((100, 60, 60, 30)), mask_rects=[(90, 50, 90, 60)])
    assert res.boxes == [] and res.identical


def test_diff_ignores_tiny_changes():
    tiny = _white()
    tiny[10:12, 10:12] = 0  # 4 pixels: below SCREEN_MIN_BOX_AREA
    assert diff_boxes(_white(), tiny).boxes == []


def test_diff_rejects_size_mismatch():
    with pytest.raises(ValueError):
        diff_boxes(np.zeros((10, 10, 3), np.uint8), np.zeros((20, 20, 3), np.uint8))


def test_static_video_has_no_events(tmp_path):
    video = _write_video(tmp_path / "v.mp4", [(4.0, _white)])
    tl = build_screen_timeline(video, tmp_path / "out", ocr=_no_ocr)
    assert tl.events == []
    assert len(tl.scenes) == 1


def test_one_small_change_is_one_local_event(tmp_path):
    video = _write_video(tmp_path / "v.mp4", [(2.0, _white), (3.0, lambda: _with_rects((100, 60, 60, 30)))])
    tl = build_screen_timeline(video, tmp_path / "out", ocr=_no_ocr)
    assert len(tl.events) == 1
    ev = tl.events[0]
    assert ev.type == "local"
    assert 1.5 <= ev.t_start <= 2.6
    x, y, w, h = ev.bboxes[0]
    assert abs(x - 100) <= 12 and abs(y - 60) <= 12
    assert ev.scene_label is None


def test_keyframes_straddle_the_change(tmp_path):
    video = _write_video(tmp_path / "v.mp4", [(2.0, _white), (3.0, lambda: _with_rects((100, 60, 60, 30)))])
    out = tmp_path / "out"
    ev = build_screen_timeline(video, out, ocr=_no_ocr).events[0]
    before = cv2.imread(str(out / ev.before_frame))
    after = cv2.imread(str(out / ev.after_frame))
    assert before.shape == (H, W, 3) and after.shape == (H, W, 3)  # full resolution, not crops
    assert before[75, 130].mean() > 200  # white where the rectangle will be
    assert after[75, 130].mean() < 60  # black once it has appeared


def test_full_screen_swap_is_a_scene_cut_with_label(tmp_path):
    def grey() -> Frame:
        return np.full((H, W, 3), 90, dtype=np.uint8)

    video = _write_video(tmp_path / "v.mp4", [(2.0, _white), (3.0, grey)])
    tl = build_screen_timeline(video, tmp_path / "out", ocr=lambda img: "Sheet2")
    assert [e.type for e in tl.events] == ["scene_cut"]
    assert "Sheet2" in tl.events[0].scene_label  # top and bottom strips are both OCR'd
    assert len(tl.scenes) == 2 and tl.scenes[1].label == tl.events[0].scene_label
    assert tl.scenes[0].t_end == pytest.approx(tl.events[0].t_start)


def test_two_changes_far_apart_are_two_events(tmp_path):
    video = _write_video(
        tmp_path / "v.mp4",
        [
            (1.0, _white),
            (3.0, lambda: _with_rects((20, 20, 50, 30))),
            (3.0, lambda: _with_rects((20, 20, 50, 30), (200, 100, 50, 30))),
        ],
    )
    tl = build_screen_timeline(video, tmp_path / "out", ocr=_no_ocr)
    assert len(tl.events) == 2
    assert tl.events[0].event_id == "ev-001" and tl.events[1].event_id == "ev-002"


def test_changes_inside_settle_window_are_one_event(tmp_path):
    video = _write_video(
        tmp_path / "v.mp4",
        [
            (1.0, _white),
            (0.5, lambda: _with_rects((20, 20, 50, 30))),
            (3.0, lambda: _with_rects((20, 20, 50, 30), (200, 100, 50, 30))),
        ],
    )
    tl = build_screen_timeline(video, tmp_path / "out", ocr=_no_ocr)
    assert len(tl.events) == 1


def test_vertical_shift_is_a_scroll(tmp_path):
    rng = np.random.default_rng(0)
    blocks = rng.integers(0, 255, (H // 6, W // 6), dtype=np.uint8)
    page = cv2.cvtColor(cv2.resize(blocks, (W, H), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
    scrolled = np.roll(page, 30, axis=0)
    video = _write_video(tmp_path / "v.mp4", [(2.0, lambda: page), (3.0, lambda: scrolled)])
    tl = build_screen_timeline(video, tmp_path / "out", ocr=_no_ocr)
    assert [e.type for e in tl.events] == ["large_shift"]


# --- adaptive evidence regressions (docs/26) ---------------------------------


def test_dropdown_opened_and_closed_is_one_transient_event(tmp_path):
    video = _write_video(
        tmp_path / "v.mp4",
        [(2.0, _white), (0.7, lambda: _with_rects((100, 40, 80, 90))), (3.0, _white)],
    )
    out = tmp_path / "out"
    tl = build_screen_timeline(video, out, ocr=_no_ocr)
    assert len(tl.events) == 1
    ev = tl.events[0]
    assert ev.transient and ev.bboxes == []
    assert ev.peak_frame is not None
    peak = cv2.imread(str(out / ev.peak_frame))
    assert peak[80, 140].mean() < 60  # the open dropdown is still inspectable


def test_committed_change_is_not_transient(tmp_path):
    video = _write_video(tmp_path / "v.mp4", [(2.0, _white), (3.0, lambda: _with_rects((100, 60, 60, 30)))])
    ev = build_screen_timeline(video, tmp_path / "out", ocr=_no_ocr).events[0]
    assert not ev.transient and ev.peak_frame is None


def _quick_pair_video(tmp_path: Path, second: Tuple[int, int, int, int]) -> Path:
    return _write_video(
        tmp_path / "v.mp4",
        [
            (1.0, _white),
            (0.5, lambda: _with_rects((20, 20, 50, 30))),
            (3.0, lambda: _with_rects((20, 20, 50, 30), second)),
        ],
    )


def test_locus_split_separates_quick_actions_in_different_places(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SCREEN_SPLIT_ON_LOCUS_SHIFT", True)
    tl = build_screen_timeline(_quick_pair_video(tmp_path, (200, 100, 50, 30)), tmp_path / "out", ocr=_no_ocr)
    assert len(tl.events) == 2


def test_locus_split_keeps_adjacent_changes_together(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SCREEN_SPLIT_ON_LOCUS_SHIFT", True)
    tl = build_screen_timeline(_quick_pair_video(tmp_path, (80, 20, 40, 30)), tmp_path / "out", ocr=_no_ocr)
    assert len(tl.events) == 1


def _scroll_page():
    rng = np.random.default_rng(0)
    blocks = rng.integers(0, 255, (H // 6, W // 6), dtype=np.uint8)
    return cv2.cvtColor(cv2.resize(blocks, (W, H), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)


def test_pure_scroll_has_no_residual_changes(tmp_path):
    page = _scroll_page()
    scrolled = np.roll(page, 30, axis=0)
    video = _write_video(tmp_path / "v.mp4", [(2.0, lambda: page), (3.0, lambda: scrolled)])
    ev = build_screen_timeline(video, tmp_path / "out", ocr=_no_ocr).events[0]
    assert ev.type == "large_shift" and ev.residual_bboxes == [] and ev.region_boxes() == []


def test_edit_right_after_a_scroll_survives_as_residual_box(tmp_path):
    page = _scroll_page()
    scrolled = np.roll(page, 30, axis=0)
    edited = scrolled.copy()
    cv2.rectangle(edited, (150, 100), (170, 112), (255, 255, 255), -1)
    video = _write_video(tmp_path / "v.mp4", [(2.0, lambda: page), (0.34, lambda: scrolled), (3.0, lambda: edited)])
    ev = build_screen_timeline(video, tmp_path / "out", ocr=_no_ocr).events[0]
    assert ev.type == "large_shift"
    assert any(abs(x - 150) <= 14 and abs(y - 100) <= 14 for x, y, _w, _h in ev.residual_bboxes)
