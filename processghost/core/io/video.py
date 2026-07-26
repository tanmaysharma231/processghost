"""Video and audio extraction utilities."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable, List

from tqdm import tqdm


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
    interval_s: float = 2.0,
    start_s: float = 0.0,
    end_s: float | None = None,
) -> List[Path]:
    """Extract frames every interval_s seconds."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = frames_dir / "frame_%06d.png"
    vf_parts = [f"fps=1/{interval_s}"]
    if end_s:
        vf_parts.append(f"trim=start={start_s}:end={end_s}")
    vf = ",".join(vf_parts)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_s),
        "-i",
        str(video_path),
        "-vf",
        vf,
        "-q:v",
        "2",
        str(pattern),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(frames_dir.glob("frame_*.png"))


def sample_frames_at_timestamps(
    video_path: Path, frames_dir: Path, timestamps: Iterable[float]
) -> List[Path]:
    """Extract specific frames via -ss seeks; slower but precise."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    outputs: List[Path] = []
    for idx, ts in enumerate(tqdm(list(timestamps), desc="frames")):
        out = frames_dir / f"frame_ts_{idx:04d}.png"
        cmd = ["ffmpeg", "-y", "-ss", str(ts), "-i", str(video_path), "-vframes", "1", str(out)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        outputs.append(out)
    return outputs


def subsample_evenly(items: List[Path], max_count: int) -> List[Path]:
    """Evenly subsample a list down to at most max_count items, always keeping the first and last.

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
