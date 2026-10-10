"""Video and audio extraction utilities."""
from __future__ import annotations

import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, NamedTuple, TypeVar

from tqdm import tqdm

T = TypeVar("T")

# Frame filenames encode their absolute video timestamp in milliseconds, e.g. "t00014000.png" ==
# 14.000s. See extract_frames_window's docstring for why this matters -- it is not just a naming
# convenience, it is what makes cross-flag frame sharing and the Sarvam OCR ZIP round-trip
# (core/llm/sarvam_client.py: ocr_extract_frames returns {filename: text}) both correct.
_FRAME_NAME_RE = re.compile(r"^t(\d{8})\.png$")


def frame_name_for(t_s: float) -> str:
    """Canonical filename encoding an absolute timestamp: frame_name_for(14.0) == "t00014000.png".
    Fixed-width zero-padded milliseconds means lexical sort == chronological sort, which
    core/llm/sarvam_client.py's _parse_ocr_output_zip relies on for its positional fallback."""
    return f"t{round(t_s * 1000):08d}.png"


def frame_timestamp_from_name(name: str) -> float:
    """Inverse of frame_name_for. Raises ValueError on a non-conforming name."""
    m = _FRAME_NAME_RE.match(name)
    if not m:
        raise ValueError(f"Not a frame filename produced by frame_name_for: {name!r}")
    return int(m.group(1)) / 1000.0


class ExtractedFrame(NamedTuple):
    path: Path
    t_s: float  # ABSOLUTE video timestamp, seconds


def probe_duration_s(video_path: Path) -> float | None:
    """ffprobe the container duration in seconds. Returns None on ANY failure (ffprobe missing,
    non-zero exit, unparseable output) rather than raising -- callers must degrade gracefully
    (see core/sop/windows.py's degenerate-window handling), since this only exists to make a
    best-effort window clamp, not to gate the pipeline."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception:
        return None


def extract_audio(video_path: Path, audio_path: Path) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        str(audio_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def extract_frames(
    video_path: Path,
    frames_dir: Path,
    interval_s: float = 4.0,
) -> List[Path]:
    """Extract frames every interval_s seconds across the whole video (the sparse uniform pass --
    now only serves Checker lead-ins and the doc appendix; dense per-flag extraction is
    extract_frames_window below).

    start_s/end_s were removed here in v2: the old range support was broken (input-level -ss
    combined with a trim filter on the already-seeked stream double-offset the window, and there
    was no -t/-to so the encode ran to EOF regardless), and nothing called it with a range. Use
    extract_frames_window for anything time-bounded.
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = frames_dir / "frame_%06d.png"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps=1/{interval_s}",
        "-q:v",
        "2",
        str(pattern),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(frames_dir.glob("frame_*.png"))


def extract_frames_window(
    video_path: Path,
    out_dir: Path,
    start_s: float,
    end_s: float,
    interval_s: float = 0.5,
    max_frames: int | None = None,
) -> List[ExtractedFrame]:
    """Extract frames densely across [start_s, end_s) for the Checker (Phase 3).

    Every frame's absolute timestamp is carried in its filename (frame_name_for) rather than
    inferred positionally by a caller, for three reasons documented in the v2 plan:
    1. Cross-flag frame sharing -- two flags whose windows overlap produce identical filenames for
       the shared timestamps, so step 1 below can skip re-extracting them entirely.
    2. The Sarvam OCR ZIP round-trip (SarvamClient.ocr_extract_frames returns {filename: text}) --
       a globally unique, self-describing filename means no side table is needed to map OCR text
       back to a timestamp, and prevents two different flags' windows both producing
       "frame_000001.png" and silently colliding inside the ZIP.
    3. Lexical sort == chronological sort (fixed-width zero-padded ms), keeping
       _parse_ocr_output_zip's positional fallback correct.

    All Checker frames for a run should live in one flat out_dir (not per-window subdirs) --
    sharing is by filename, so subdirs would defeat it.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Grid-quantize to the global frame grid so different flags/windows that overlap produce
    # IDENTICAL timestamps (and therefore identical filenames) for their shared portion.
    start = max(0.0, math.floor(max(0.0, start_s) / interval_s) * interval_s)
    end = math.ceil(end_s / interval_s) * interval_s
    if end <= start:
        return []

    n_frames = int(round((end - start) / interval_s))
    timestamps = [start + i * interval_s for i in range(n_frames)]
    expected_names = [frame_name_for(t) for t in timestamps]

    # Sharing/dedupe check: if every expected file already exists (from a prior overlapping
    # window), return them immediately -- zero ffmpeg invocations.
    if all((out_dir / name).exists() for name in expected_names):
        result = [ExtractedFrame(out_dir / name, t) for name, t in zip(expected_names, timestamps)]
        return _apply_max_frames(result, max_frames)

    scratch = out_dir / f"_scratch_{round(start * 1000)}_{round(end * 1000)}"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        pattern = scratch / "f_%06d.png"
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-i",
            str(video_path),
            "-t",
            str(end - start),
            "-vf",
            f"fps=1/{interval_s}",
            "-q:v",
            "2",
            str(pattern),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        produced = sorted(scratch.glob("f_*.png"))
        result: List[ExtractedFrame] = []
        for i, src in enumerate(produced):
            if i >= len(timestamps):
                break
            t = timestamps[i]
            dest = out_dir / frame_name_for(t)
            if not dest.exists():
                src.replace(dest)
            result.append(ExtractedFrame(dest, t))
        return _apply_max_frames(result, max_frames)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _apply_max_frames(
    frames: List[ExtractedFrame], max_frames: int | None
) -> List[ExtractedFrame]:
    if max_frames is None:
        return frames
    return subsample_evenly(frames, max_frames)


def sample_frames_at_timestamps(
    video_path: Path, frames_dir: Path, timestamps: Iterable[float]
) -> List[Path]:
    """Extract specific frames via -ss seeks; slower but precise. Superseded by
    extract_frames_window for the Checker's windowed use case (one ffmpeg process per timestamp
    here vs. one process per window there) -- kept only because it may still suit a genuinely
    sparse, non-contiguous timestamp list. Not currently called by the pipeline."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    outputs: List[Path] = []
    for idx, ts in enumerate(tqdm(list(timestamps), desc="frames")):
        out = frames_dir / f"frame_ts_{idx:04d}.png"
        cmd = ["ffmpeg", "-y", "-ss", str(ts), "-i", str(video_path), "-vframes", "1", str(out)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        outputs.append(out)
    return outputs


def subsample_evenly(items: List[T], max_count: int) -> List[T]:
    """Evenly subsample a list down to at most max_count items, always keeping the first and last.

    Widened from List[Path] to List[T] in v2 so the Checker can subsample List[ExtractedFrame]
    directly, keeping each frame paired with its timestamp -- no behavior change otherwise.

    Used to cap the number of frames sent to a vision model per run without a full scene-change
    detection pass -- see Process_ghost/docs/09-visual-gap-filler.md for the fuller design this is
    a placeholder for. If max_count <= 0 or the list already fits, it is returned unchanged.
    """
    n = len(items)
    if max_count <= 0 or n <= max_count:
        return items
    if max_count == 1:
        return [items[0]]
    step = (n - 1) / (max_count - 1)
    indices = sorted({round(i * step) for i in range(max_count)})
    return [items[i] for i in indices]
