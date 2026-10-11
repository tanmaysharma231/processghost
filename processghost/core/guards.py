"""Input validation: reject a recording that cannot or should not be processed BEFORE any money is
spent or any file is copied. Everything here is cheap and local (a stat and one ffprobe call).

Limits live in core/config.py (MAX_VIDEO_BYTES, MAX_VIDEO_DURATION_S). They are deliberately tight:
the pipeline has only been validated on recordings of tens of seconds (docs/scope-and-limits.md), and
cost grows with length, so an over-long file fails fast with a clear message instead of running up a
bill. Raise the limits knowingly.
"""
from __future__ import annotations

from pathlib import Path

from core import config
from core.io.video import probe_duration_s


class InputRejected(ValueError):
    """The input recording was refused; the message says why and what limit applies."""


def validate_input(video_path: Path) -> float:
    """Raise InputRejected if `video_path` is unusable. Returns the duration in seconds."""
    path = Path(video_path)
    if not path.is_file():
        raise InputRejected(f"input is not a file: {path}")
    size = path.stat().st_size
    if size == 0:
        raise InputRejected(f"input file is empty: {path}")
    if size > config.MAX_VIDEO_BYTES:
        raise InputRejected(
            f"input is {size / 1e6:.0f} MB; the limit is {config.MAX_VIDEO_BYTES / 1e6:.0f} MB "
            "(config.MAX_VIDEO_BYTES)"
        )
    duration = probe_duration_s(path)
    if duration is None:
        raise InputRejected(f"could not read the video's duration (corrupt file, or ffprobe is not on PATH): {path}")
    if duration <= 0:
        raise InputRejected(f"video has no duration: {path}")
    if duration > config.MAX_VIDEO_DURATION_S:
        raise InputRejected(
            f"video is {duration:.0f} s long; the limit is {config.MAX_VIDEO_DURATION_S:.0f} s "
            "(config.MAX_VIDEO_DURATION_S). The pipeline is only validated on short recordings."
        )
    return duration
