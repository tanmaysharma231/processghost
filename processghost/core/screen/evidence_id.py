"""Stable, content-based evidence identifiers (ADR 0012).

An evidence id names one retrievable artifact (a frame, a crop, a comparison) by WHAT it is, not where it
was written: the video's fingerprint, the kind of artifact, and its parameters. Asking for the same thing
twice therefore yields the same id, on this run or a later one, which is what lets a cache hit cost
nothing and lets a finding point at its evidence reproducibly.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional

from core.models.screen import Box, ScreenEvent

_HEAD_TAIL_BYTES = 1024 * 1024


def video_fingerprint(path: Path) -> str:
    """Cheap content fingerprint: file size plus the first and last megabyte. Not a full hash (a 500 MB
    recording would take seconds), but two different recordings do not collide in practice."""
    size = path.stat().st_size
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(_HEAD_TAIL_BYTES))
        if size > 2 * _HEAD_TAIL_BYTES:
            f.seek(-_HEAD_TAIL_BYTES, 2)
            h.update(f.read(_HEAD_TAIL_BYTES))
    return h.hexdigest()[:16]


def evidence_id(video_sha: Optional[str], kind: str, *parts) -> str:
    """`parts` are times (rounded to centiseconds), boxes, pads, widths or names, in a fixed order."""
    norm = [f"{p:.2f}" if isinstance(p, float) else str(p) for p in parts]
    return "evd-" + hashlib.sha1("|".join([video_sha or "", kind, *norm]).encode()).hexdigest()[:10]


def keyframe_id(video_sha: Optional[str], kind: str, frame_path: str) -> str:
    return evidence_id(video_sha, kind, frame_path)


def event_evidence_ids(video_sha: Optional[str], ev: ScreenEvent) -> List[str]:
    """Ids of the keyframes the timeline saved for an event: before, after, and the peak when transient."""
    ids = [keyframe_id(video_sha, "keyframe_before", ev.before_frame), keyframe_id(video_sha, "keyframe_after", ev.after_frame)]
    if ev.peak_frame:
        ids.append(keyframe_id(video_sha, "keyframe_peak", ev.peak_frame))
    return ids


def box_part(box: Optional[Box]) -> str:
    return "-" if box is None else ",".join(str(int(v)) for v in box)
