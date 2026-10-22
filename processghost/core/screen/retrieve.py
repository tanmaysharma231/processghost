"""Bounded evidence retrieval over a recording (ADR 0012): the video as searchable evidence.

Deterministic functions that hand back frames, crops, comparisons and episode summaries, each with a stable
evidence id, its timestamp and coordinates. Nothing here calls a model. Two properties matter:

- Caching: an artifact is named by its content-based id and written once. Asking again returns the same
  reference with no decode and no charge, in this process or a later one.
- Budget: every NEW artifact is charged against an `InspectionBudget`. When it is spent the call returns a
  `Refusal` (recorded in `Retriever.refusals`) instead of evidence, so a cap is a visible limit on what was
  looked at and never mistaken for "nothing there".

The Checker still escalates deterministically (ADR 0007); a model that picks which of these to call is an
experiment, not built. These are the functions such an experiment would call.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Union

import cv2
import numpy as np

from core import config
from core.models.screen import Box, EpisodeResult, EvidenceRef, Refusal, RegionsResult, ScreenTimeline
from core.models.tokens import TranscriptSegment
from core.screen.diff import diff_boxes
from core.screen.evidence import _fit, _label_bar, _padded_crop, _resize_w
from core.screen.evidence_id import box_part, evidence_id, keyframe_id, video_fingerprint
from core.screen.regions import plan_regions

_FRAME_CACHE_SIZE = 8
Result = Union[EvidenceRef, Refusal]


class InspectionBudget:
    """Counts new artifacts handed out. `charge` returns None when allowed, or the reason it is refused."""

    def __init__(
        self,
        images: int = config.INSPECT_MAX_IMAGES,
        crops: int = config.INSPECT_MAX_CROPS,
        expansions: int = config.INSPECT_MAX_EXPANSIONS,
        retries_per_episode: int = config.INSPECT_MAX_RETRIES_PER_EPISODE,
    ) -> None:
        self.limits = {"images": images, "crops": crops, "expansions": expansions}
        self.used = {k: 0 for k in self.limits}
        self.retries_per_episode = retries_per_episode
        self.inspections: Dict[str, int] = {}

    def charge(self, kind: str, n: int = 1) -> Optional[str]:
        if self.used[kind] + n > self.limits[kind]:
            return f"{kind} budget exhausted ({self.used[kind]} of {self.limits[kind]} used)"
        self.used[kind] += n
        return None

    def charge_inspection(self, key: str) -> Optional[str]:
        count = self.inspections.get(key, 0)
        if count > self.retries_per_episode:
            return f"episode {key} already inspected {count} times (limit {self.retries_per_episode} retries)"
        self.inspections[key] = count + 1
        return None


class Retriever:
    def __init__(
        self,
        video_path: Path,
        out_dir: Path,
        *,
        timeline: Optional[ScreenTimeline] = None,
        timeline_dir: Optional[Path] = None,
        transcript: Optional[Sequence[TranscriptSegment]] = None,
        budget: Optional[InspectionBudget] = None,
        on_call: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.video_path, self.out_dir = video_path, out_dir
        self.timeline, self.timeline_dir = timeline, timeline_dir
        self.transcript = list(transcript or [])
        self.budget = budget or InspectionBudget()
        self.on_call = on_call
        self.video_sha = (timeline.video_sha if timeline else None) or video_fingerprint(video_path)
        self.decodes = 0  # frames actually decoded from the video (cache hits do not count)
        self.refusals: List[Refusal] = []
        self._frames: Dict[int, np.ndarray] = {}
        self._results: Dict[str, object] = {}
        out_dir.mkdir(parents=True, exist_ok=True)

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"cannot open video: {video_path}")
        self._fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
        self.duration_s = timeline.duration_s if timeline else self._n_frames / self._fps

    # -- plumbing ---------------------------------------------------------------------------

    def _emit(self, tool: str, started: float, evidence: Optional[str], *, hit: bool = False, refused: bool = False) -> None:
        if self.on_call:
            self.on_call(
                {
                    "event": "retrieve", "stage": "retrieve", "tool": tool, "evidence_id": evidence,
                    "cache_hit": hit, "refused": refused, "latency_ms": int((time.perf_counter() - started) * 1000),
                }
            )

    def _refuse(self, tool: str, started: float, reason: str) -> Refusal:
        refusal = Refusal(tool=tool, reason=reason)
        self.refusals.append(refusal)
        self._emit(tool, started, None, refused=True)
        return refusal

    def _frame(self, t: float) -> np.ndarray:
        idx = min(max(int(round(t * self._fps)), 0), max(self._n_frames - 1, 0))
        if idx not in self._frames:
            cap = cv2.VideoCapture(str(self.video_path))
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            cap.release()
            if not ok:
                raise RuntimeError(f"cannot decode frame at t={t:.2f}s")
            self.decodes += 1
            if len(self._frames) >= _FRAME_CACHE_SIZE:
                self._frames.pop(next(iter(self._frames)))
            self._frames[idx] = frame
        return self._frames[idx]

    def _event_at(self, t: float) -> Optional[str]:
        if self.timeline is None:
            return None
        for ev in self.timeline.events:
            if ev.t_start - 1.0 <= t <= ev.t_end + 1.0:
                return ev.event_id
        return None

    def _artifact(
        self, tool: str, kind: str, eid: str, charge: str, make: Callable[[], np.ndarray], t: float,
        t_end: Optional[float] = None, box: Optional[Box] = None,
    ) -> Result:
        started = time.perf_counter()
        name = f"{eid}.png"
        ref = EvidenceRef(
            evidence_id=eid, kind=kind, t_s=round(t, 2), t_end=None if t_end is None else round(t_end, 2), box=box,
            path=name, event_id=self._event_at(t), video_sha=self.video_sha,
        )
        if (self.out_dir / name).exists():
            self._emit(tool, started, eid, hit=True)
            return ref
        reason = self.budget.charge(charge)
        if reason:
            return self._refuse(tool, started, reason)
        cv2.imwrite(str(self.out_dir / name), make())
        self._emit(tool, started, eid)
        return ref

    # -- the tools ---------------------------------------------------------------------------

    def get_context_frame(self, t: float, width: Optional[int] = None) -> Result:
        """A legible full-screen frame at `t`, downscaled to `width` (default: the Checker's full-frame width)."""
        w = width or config.SCREEN_EVIDENCE_FULL_WIDTH
        eid = evidence_id(self.video_sha, "context_frame", float(t), w)
        return self._artifact("get_context_frame", "context_frame", eid, "images", lambda: _resize_w(self._frame(t), w), t)

    def get_crop(self, t: float, box: Box, pad: Optional[int] = None) -> Result:
        """A full-resolution crop around `box`, padded so nearby labels come with it. Ask for a context
        frame as well when a crop alone lacks its headers."""
        p = config.SCREEN_CROP_PAD_PX if pad is None else pad
        eid = evidence_id(self.video_sha, "crop", float(t), box_part(box), p)
        return self._artifact("get_crop", "crop", eid, "crops", lambda: _padded_crop(self._frame(t), box, p), t, box=box)

    def compare_frames(self, t1: float, t2: float, box: Optional[Box] = None) -> Result:
        """Before and after stacked in one image, labelled with their times; cropped to `box` when given."""

        def make() -> np.ndarray:
            a, b = self._frame(t1), self._frame(t2)
            if box is not None:
                a, b = _padded_crop(a, box, config.SCREEN_CROP_PAD_PX), _padded_crop(b, box, config.SCREEN_CROP_PAD_PX)
            width = max(a.shape[1], b.shape[1], 240)
            return cv2.vconcat(
                [_label_bar(width, f"t={t1:.2f}s before"), _fit(a, width), _label_bar(width, f"t={t2:.2f}s after"), _fit(b, width)]
            )

        eid = evidence_id(self.video_sha, "compare", float(t1), float(t2), box_part(box))
        return self._artifact("compare_frames", "compare", eid, "images", make, t1, t_end=t2, box=box)

    def get_changed_regions(self, t1: float, t2: float, include_frames: bool = False) -> Union[RegionsResult, Refusal]:
        """Diff metadata between two moments. Free unless `include_frames` asks for the two frames too."""
        started = time.perf_counter()
        eid = evidence_id(self.video_sha, "changed_regions", float(t1), float(t2))
        result = self._results.get(eid)
        hit = result is not None
        if result is None:
            res = diff_boxes(self._frame(t1), self._frame(t2), config.SCREEN_MASK_RECTS)
            h, w = self._frame(t1).shape[:2]
            groups = plan_regions(res.boxes, config.SCREEN_RECORD_MAX_REGIONS, w, h, config.SCREEN_REGION_MAX_CROP_FRAC)
            result = RegionsResult(
                evidence_id=eid, t1=round(t1, 2), t2=round(t2, 2), changed_frac=round(res.changed_frac, 5),
                boxes=res.boxes, groups=[g.box for g in groups],
            )
            self._results[eid] = result
        self._emit("get_changed_regions", started, eid, hit=hit)
        if not include_frames:
            return result
        frames: List[EvidenceRef] = []
        for t in (t1, t2):
            got = self.get_context_frame(t)
            if isinstance(got, Refusal):
                return got
            frames.append(got)
        return result.model_copy(update={"frames": frames})

    def _episode(self, tool: str, t0: float, t1: float, detail: str, key_extra: str = "") -> Union[EpisodeResult, Refusal]:
        started = time.perf_counter()
        eid = evidence_id(self.video_sha, "episode", float(t0), float(t1), detail + key_extra)
        cached = self._results.get(eid)
        if cached is not None:
            self._emit(tool, started, eid, hit=True)
            return cached
        events = [e for e in (self.timeline.events if self.timeline else []) if e.t_end >= t0 and e.t_start <= t1]
        frames: List[EvidenceRef] = []
        if detail == "keyframes" and events and self.timeline_dir is not None:
            reason = self.budget.charge("images", 2)
            if reason:
                return self._refuse(tool, started, reason)
            for ev, attr, kind in ((events[0], "before_frame", "keyframe_before"), (events[-1], "after_frame", "keyframe_after")):
                rel = getattr(ev, attr)
                frames.append(
                    EvidenceRef(
                        evidence_id=keyframe_id(self.video_sha, kind, rel), kind="keyframe", t_s=ev.t_start,
                        path=str(self.timeline_dir / rel), event_id=ev.event_id, video_sha=self.video_sha,
                    )
                )
        result = EpisodeResult(
            evidence_id=eid, t_start=round(t0, 2), t_end=round(t1, 2), event_ids=[e.event_id for e in events],
            transcript=[s.text for s in self.transcript if s.end_s >= t0 and s.start_s <= t1], frames=frames,
        )
        self._results[eid] = result
        self._emit(tool, started, eid)
        return result

    def inspect_episode(self, t0: float, t1: float, detail: str = "index") -> Union[EpisodeResult, Refusal]:
        """Events, spoken text and (at detail "keyframes") the first before / last after keyframe for an
        interval. Re-inspecting the same interval at a new detail level counts as a retry."""
        if detail not in ("index", "keyframes"):
            raise ValueError("detail must be 'index' or 'keyframes'")
        started = time.perf_counter()
        eid = evidence_id(self.video_sha, "episode", float(t0), float(t1), detail)
        if eid not in self._results:
            reason = self.budget.charge_inspection(f"{t0:.2f}-{t1:.2f}")
            if reason:
                return self._refuse("inspect_episode", started, reason)
        return self._episode("inspect_episode", t0, t1, detail)

    def expand_interval(self, t0: float, t1: float, lead: float, lag: float) -> Union[EpisodeResult, Refusal]:
        """The same kind of summary over a wider window (clipped to the recording). Counts against the
        expansion budget."""
        started = time.perf_counter()
        w0, w1 = max(0.0, t0 - lead), min(self.duration_s, t1 + lag)
        eid = evidence_id(self.video_sha, "episode", float(w0), float(w1), "index")
        if eid not in self._results:
            reason = self.budget.charge("expansions")
            if reason:
                return self._refuse("expand_interval", started, reason)
        return self._episode("expand_interval", w0, w1, "index")
