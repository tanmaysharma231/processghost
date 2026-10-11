"""Data retention: remove what a run left on disk.

A run keeps a copy of the video, the extracted audio and every frame it sent to a model; the model
cache keeps transcripts and model outputs. Recordings may show client screens, so all of it should be
deletable on request and on a schedule. Nothing here deletes unless asked: `plan_cleanup` only lists,
`apply_cleanup` acts on a plan.

What is "media": the video copy, audio, and every directory of frames or keyframes. The text outputs
(sop.md, sop.json, build_spec.json, manifest.json, trace.jsonl, run.json) are kept by `strip_media`,
though note they still contain the transcript and anything the models quoted from the screen.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import List, NamedTuple, Optional

from core import config

MEDIA_FILES = ("video.mp4", "audio.wav")
MEDIA_DIRS = ("frames", "checker_frames", "screen")


class Action(NamedTuple):
    path: Path
    kind: str  # "run" (whole directory), "media" (media inside a kept run), "cache" (cache file)
    reason: str


def strip_media(run_dir: Path) -> List[Path]:
    """Delete the video copy, audio and frame directories from one run. Returns what was removed."""
    removed: List[Path] = []
    for name in MEDIA_FILES:
        p = run_dir / name
        if p.is_file():
            p.unlink()
            removed.append(p)
    for name in MEDIA_DIRS:
        d = run_dir / name
        if d.is_dir():
            shutil.rmtree(d)
            removed.append(d)
    return removed


def _age_days(path: Path, now: float) -> float:
    return (now - path.stat().st_mtime) / 86400.0


def plan_cleanup(
    runs_dir: Path,
    cache_dir: Optional[Path],
    older_than_days: float,
    media_only: bool = False,
    include_cache: bool = False,
    now: Optional[float] = None,
) -> List[Action]:
    """List what a cleanup would remove: run directories older than `older_than_days` (or, with
    `media_only`, just their media), and optionally cache files older than the same age. Names that
    do not look like run directories are never touched."""
    now = time.time() if now is None else now
    actions: List[Action] = []
    if runs_dir.is_dir():
        for run in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
            if _age_days(run, now) < older_than_days:
                continue
            if media_only:
                present = [run / n for n in (*MEDIA_FILES, *MEDIA_DIRS) if (run / n).exists()]
                if present:
                    actions.append(Action(run, "media", f"{len(present)} media item(s), run is {_age_days(run, now):.0f} days old"))
            else:
                actions.append(Action(run, "run", f"{_age_days(run, now):.0f} days old"))
    if include_cache and cache_dir is not None and cache_dir.is_dir():
        for f in sorted(cache_dir.rglob("*.json")):
            if _age_days(f, now) >= older_than_days:
                actions.append(Action(f, "cache", f"{_age_days(f, now):.0f} days old"))
    return actions


def apply_cleanup(actions: List[Action]) -> int:
    done = 0
    for a in actions:
        if a.kind == "run":
            shutil.rmtree(a.path, ignore_errors=True)
        elif a.kind == "media":
            strip_media(a.path)
        elif a.kind == "cache":
            a.path.unlink(missing_ok=True)
        done += 1
    return done


def default_dirs() -> tuple[Path, Path]:
    return config.RUNS_DIR, config.CACHE_DIR
