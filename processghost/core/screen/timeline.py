"""Screen timeline (docs/22 stage 0b): turn a screen recording into a list of discrete change
events, using only pixels. Plain OpenCV, no model calls except an optional local OCR for labelling
context switches.

Why this shape: a screen is static between discrete jumps, so "find when it changed, then look at a
few images" captures nearly all the information in the video. Samples are taken at SCREEN_FPS; a run
of changed samples is one event, which ends after SCREEN_SETTLE_S of stillness. Each event keeps a
full-resolution `before` frame (last still frame before the change) and `after` frame (the settled
result) -- full frames, not just crops, because which sheet/file/page you are on is only visible in
the chrome around the change.

Event type (see config.py for why changed-pixel fraction alone is not used):
- large_shift: the after frame is the before frame shifted vertically (a scroll).
- scene_cut: the changed region spans >= SCREEN_SCENE_COVERAGE of the frame AND a real share of its
  pixels changed (a context switch). Scattered small edits (a pasted cell plus the totals it feeds)
  span a wide box but change few pixels, so they stay local.
- local: anything smaller (a cell edited, a field filled, a dialog button pressed).
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from core import config
from core.io.video import frame_name_for
from core.models.screen import Box, Scene, ScreenEvent, ScreenTimeline
from core.screen.diff import diff_boxes, normalize_frame
from core.screen.ocr import OcrFn, get_ocr

_Sample = Tuple[float, np.ndarray]  # (t_s, BGR frame)


def _read_samples(video_path: Path, fps: float) -> Tuple[List[_Sample], float, int, int]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    native = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, int(round(native / fps)))
    samples: List[_Sample] = []
    idx = 0
    while True:
        if not cap.grab():
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if ok:
                samples.append((idx / native, frame))
        idx += 1
    cap.release()
    return samples, idx / native, width, height


def _bbox_coverage(boxes: Sequence[Box], width: int, height: int) -> float:
    if not boxes:
        return 0.0
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return ((x1 - x0) * (y1 - y0)) / float(width * height)


def _is_scroll(before: np.ndarray, after: np.ndarray, changed_frac: float) -> bool:
    """True if `after` is `before` shifted vertically: phase-correlate the two, translate `before`
    by the measured shift, and require the residual difference to be much smaller than the raw one."""
    if changed_frac < 0.02:
        return False
    a, _ = normalize_frame(before)
    b, _ = normalize_frame(after)
    (dx, dy), response = cv2.phaseCorrelate(np.float32(a), np.float32(b))
    if abs(dy) < config.SCREEN_SCROLL_MIN_SHIFT_PX or response < config.SCREEN_SCROLL_MIN_RESPONSE:
        return False
    shifted = cv2.warpAffine(a, np.float32([[1, 0, dx], [0, 1, dy]]), (a.shape[1], a.shape[0]))
    residual = float(np.count_nonzero(cv2.absdiff(shifted, b) > config.SCREEN_PIXEL_THRESH)) / a.size
    return residual < 0.5 * changed_frac


def chrome_text(frame: np.ndarray, ocr: OcrFn) -> str:
    h = frame.shape[0]
    top = frame[: int(h * config.SCREEN_TOP_STRIP_PCT)]
    bottom = frame[int(h * (1.0 - config.SCREEN_BOTTOM_STRIP_PCT)) :]
    return " | ".join(t for t in (ocr(top), ocr(bottom)) if t)


def build_screen_timeline(
    video_path: Path,
    out_dir: Path,
    *,
    fps: float = config.SCREEN_FPS,
    mask_rects: Sequence[Box] = config.SCREEN_MASK_RECTS,
    ocr: Optional[OcrFn] = None,
) -> ScreenTimeline:
    """Pure function of the video and config (plus `ocr`, default: local RapidOCR if installed).
    Writes full-resolution before/after keyframes to out_dir/keyframes/ and returns the timeline;
    the caller persists it (see scripts/screen_timeline.py)."""
    ocr = ocr if ocr is not None else get_ocr()
    key_dir = out_dir / "keyframes"
    key_dir.mkdir(parents=True, exist_ok=True)

    samples, duration_s, width, height = _read_samples(video_path, fps)
    timeline = ScreenTimeline(
        video=str(video_path),
        duration_s=duration_s,
        width=width,
        height=height,
        params={
            "fps": fps,
            "settle_s": config.SCREEN_SETTLE_S,
            "pixel_thresh": config.SCREEN_PIXEL_THRESH,
            "min_box_area": config.SCREEN_MIN_BOX_AREA,
            "scene_coverage": config.SCREEN_SCENE_COVERAGE,
        },
    )
    if len(samples) < 2:
        return timeline

    # Pass 1: per-sample activity (did this sample differ from the previous one?).
    active: List[bool] = [False]
    for i in range(1, len(samples)):
        res = diff_boxes(samples[i - 1][1], samples[i][1], mask_rects)
        active.append(res.changed_frac >= config.SCREEN_ACTIVE_FRAC)
        timeline.activity.append((round(samples[i][0], 3), round(res.changed_frac, 5), len(res.boxes)))

    # Pass 2: group runs of active samples into events; a run ends after SETTLE_S of stillness.
    runs: List[Tuple[int, int]] = []  # inclusive sample indices (first_active, last_active)
    start: Optional[int] = None
    last_active = -1
    for i in range(1, len(samples)):
        gap = samples[i][0] - samples[last_active][0] if last_active >= 0 else 0.0
        settled = gap >= config.SCREEN_SETTLE_S - 1e-6
        if active[i]:
            if start is not None and settled:
                runs.append((start, last_active))  # the previous burst had already gone still
                start = None
            if start is None:
                start = i
            last_active = i
        elif start is not None and settled:
            runs.append((start, last_active))
            start = None
    if start is not None:
        runs.append((start, last_active))

    for n, (first, last) in enumerate(runs, start=1):
        t_before, before = samples[first - 1]
        t_after, after = samples[last]
        net = diff_boxes(before, after, mask_rects)
        coverage = _bbox_coverage(net.boxes, width, height)
        if _is_scroll(before, after, net.changed_frac):
            etype = "large_shift"
        elif coverage >= config.SCREEN_SCENE_COVERAGE and net.changed_frac >= config.SCREEN_SCENE_MIN_CHANGED:
            etype = "scene_cut"
        else:
            etype = "local"

        before_name, after_name = frame_name_for(t_before), frame_name_for(t_after)
        cv2.imwrite(str(key_dir / before_name), before)
        cv2.imwrite(str(key_dir / after_name), after)
        timeline.events.append(
            ScreenEvent(
                event_id=f"ev-{n:03d}",
                t_start=round(samples[first][0], 3),
                t_end=round(t_after, 3),
                type=etype,
                changed_pct=round(net.changed_frac * 100, 3),
                coverage_pct=round(coverage * 100, 2),
                bboxes=net.boxes,
                before_frame=f"keyframes/{before_name}",
                after_frame=f"keyframes/{after_name}",
                scene_label=chrome_text(after, ocr) if (etype == "scene_cut" and ocr) else None,
            )
        )

    timeline.scenes = _build_scenes(timeline, samples[0][1], samples[-1][0], ocr)
    return timeline


def _build_scenes(timeline: ScreenTimeline, first_frame: np.ndarray, t_last: float, ocr: Optional[OcrFn]) -> List[Scene]:
    """Scenes are the spans between scene_cut events. The first one is labelled from the first frame."""
    scenes = [
        Scene(scene_id="sc-001", t_start=0.0, t_end=t_last, label=chrome_text(first_frame, ocr) if ocr else None)
    ]
    for ev in timeline.events:
        if ev.type == "scene_cut":
            scenes[-1].t_end = ev.t_start
            scenes.append(
                Scene(scene_id=f"sc-{len(scenes) + 1:03d}", t_start=ev.t_start, t_end=t_last, label=ev.scene_label)
            )
        scenes[-1].event_ids.append(ev.event_id)
    return scenes
