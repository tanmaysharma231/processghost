"""Checker evidence assembly from screen events (docs/22 stage 3): context AND detail.

Per event the Checker is given
- a full after-frame, downscaled (which sheet / file / page you are on is only visible here),
- a strip of numbered tiles: padded crops of the largest changed regions at full resolution, before
  on top and after below (the detail: the exact cell or field),
- for a context switch, a low-resolution before-frame,
- OCR text of the chrome strips (title, tabs, status bar) and of each changed region, so a text-only
  tier can answer "which sheet?" without any image at all.

Pure functions of the timeline and its keyframes; OCR is injected.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, NamedTuple, Optional

import cv2
import numpy as np

from core import config
from core.models.screen import ScreenEvent
from core.screen.evidence_id import box_part, evidence_id
from core.screen.ocr import OcrFn
from core.screen.regions import plan_regions
from core.screen.timeline import chrome_text


class EvidencePack(NamedTuple):
    images: List[Path]  # ready to attach to a vision call, capped at SCREEN_EVIDENCE_MAX_IMAGES
    ocr_text: str  # labelled OCR text, for text-only tiers
    event_ids: List[str]
    after_frames: List[Path]  # full-res keyframes used, recorded as the Resolution's evidence
    description: str  # what each image is, for the prompt
    evidence_ids: tuple = ()  # stable ids (core/screen/evidence_id.py) of the artifacts above, for traces


def select_events(flag_event_ids: List[str], all_events: List[ScreenEvent], center_t: float, level: int) -> List[ScreenEvent]:
    """Level 0: the attached event nearest `center_t`. Level 1: that event plus
    SCREEN_ESCALATION_EVENTS events either side of it in the full timeline. Deterministic: the
    model never decides how much context it gets."""
    attached = [e for e in all_events if e.event_id in set(flag_event_ids)]
    if not attached:
        return []
    nearest = min(attached, key=lambda e: abs((e.t_start + e.t_end) / 2.0 - center_t))
    if level == 0:
        return [nearest]
    ordered = sorted(all_events, key=lambda e: e.t_start)
    i = next(k for k, e in enumerate(ordered) if e.event_id == nearest.event_id)
    n = config.SCREEN_ESCALATION_EVENTS
    return ordered[max(0, i - n) : i + n + 1]


def _resize_w(img: np.ndarray, width: int) -> np.ndarray:
    if img.shape[1] <= width:
        return img
    return cv2.resize(img, (width, int(img.shape[0] * width / img.shape[1])), interpolation=cv2.INTER_AREA)


def _padded_crop(img: np.ndarray, box, pad: int) -> np.ndarray:
    x, y, w, h = box
    h_img, w_img = img.shape[:2]
    return img[max(0, y - pad) : min(h_img, y + h + pad), max(0, x - pad) : min(w_img, x + w + pad)]


def _label_bar(width: int, text: str) -> np.ndarray:
    bar = np.full((24, width, 3), 255, dtype=np.uint8)
    cv2.putText(bar, text, (4, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    return bar


def _event_regions(after: np.ndarray, boxes) -> list:
    """Region group boxes for one event: merged by proximity, never dropped by size (ADR 0010)."""
    h_img, w_img = after.shape[:2]
    groups = plan_regions(boxes, config.SCREEN_EVIDENCE_MAX_BOXES, w_img, h_img, config.SCREEN_REGION_MAX_CROP_FRAC)
    return [g.box for g in groups]


def _tile_strip(before: np.ndarray, after: np.ndarray, boxes, event_id: str) -> Optional[np.ndarray]:
    tiles = []
    for n, box in enumerate(_event_regions(after, boxes), start=1):
        b_crop = _padded_crop(before, box, config.SCREEN_CROP_PAD_PX)
        a_crop = _padded_crop(after, box, config.SCREEN_CROP_PAD_PX)
        if b_crop.size == 0 or a_crop.size == 0:
            continue
        width = max(b_crop.shape[1], 240)
        pair = cv2.vconcat([_fit(b_crop, width), _fit(a_crop, width)])
        tiles.append(cv2.vconcat([_label_bar(width, f"{event_id} tile {n}: top=before bottom=after"), pair]))
    if not tiles:
        return None
    height = max(t.shape[0] for t in tiles)
    padded = [cv2.copyMakeBorder(t, 0, height - t.shape[0], 0, 6, cv2.BORDER_CONSTANT, value=(255, 255, 255)) for t in tiles]
    return cv2.hconcat(padded)


def _fit(img: np.ndarray, width: int) -> np.ndarray:
    pad = width - img.shape[1]
    return cv2.copyMakeBorder(img, 0, 0, 0, pad, cv2.BORDER_CONSTANT, value=(255, 255, 255)) if pad > 0 else img


def build_evidence(
    events: List[ScreenEvent],
    timeline_dir: Path,
    out_dir: Path,
    ocr: Optional[OcrFn] = None,
    video_sha: Optional[str] = None,
) -> EvidencePack:
    out_dir.mkdir(parents=True, exist_ok=True)
    ids: List[str] = []
    images: List[Path] = []
    ocr_parts: List[str] = []
    desc: List[str] = []
    after_frames: List[Path] = []

    for ev in events:
        before = cv2.imread(str(timeline_dir / ev.before_frame))
        after = cv2.imread(str(timeline_dir / ev.after_frame))
        if before is None or after is None:
            continue
        after_frames.append(timeline_dir / ev.after_frame)

        full = out_dir / f"{ev.event_id}_full.png"
        cv2.imwrite(str(full), _resize_w(after, config.SCREEN_EVIDENCE_FULL_WIDTH))
        images.append(full)
        ids.append(evidence_id(video_sha, "context_frame", ev.after_frame, config.SCREEN_EVIDENCE_FULL_WIDTH))
        desc.append(f"{ev.event_id} ({ev.type}, t={ev.t_start:.1f}s): full after-frame, downscaled")

        strip = _tile_strip(before, after, ev.region_boxes(), ev.event_id)
        if strip is not None:
            tiles = out_dir / f"{ev.event_id}_tiles.png"
            cv2.imwrite(str(tiles), strip)
            images.append(tiles)
            ids.append(evidence_id(video_sha, "tiles", ev.event_id, *[box_part(b) for b in ev.region_boxes()]))
            desc.append(f"{ev.event_id}: numbered tiles of the changed regions (top = before, bottom = after)")

        if ev.type == "scene_cut":
            low = out_dir / f"{ev.event_id}_before.png"
            cv2.imwrite(str(low), _resize_w(before, config.SCREEN_EVIDENCE_BEFORE_WIDTH))
            images.append(low)
            ids.append(evidence_id(video_sha, "before_frame", ev.before_frame, config.SCREEN_EVIDENCE_BEFORE_WIDTH))
            desc.append(f"{ev.event_id}: low-resolution frame from before the switch")

        if ocr is not None:
            parts = [f"[{ev.event_id} screen chrome (title, tabs, status bar)] {chrome_text(after, ocr) or '(nothing legible)'}"]
            for n, box in enumerate(_event_regions(after, ev.region_boxes()), 1):
                crop = _padded_crop(after, box, config.SCREEN_CROP_PAD_PX)
                if crop.size:
                    parts.append(f"[{ev.event_id} changed region {n}, after] {ocr(crop) or '(nothing legible)'}")
            ocr_parts.append("\n".join(parts))

    return EvidencePack(
        images=images[: config.SCREEN_EVIDENCE_MAX_IMAGES],
        ocr_text="\n\n".join(ocr_parts),
        event_ids=[e.event_id for e in events],
        after_frames=after_frames,
        description="\n".join(f"- {d}" for d in desc),
        evidence_ids=tuple(ids),
    )
