"""Tests for core/io/video.py's windowed frame extraction (extract_frames_window) and the
timestamp<->filename encoding it depends on. Most of these need a real ffmpeg binary and a real
video fixture; they're skipped when either isn't available rather than failing the suite.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from core.io.video import (
    ExtractedFrame,
    extract_frames_window,
    frame_name_for,
    frame_timestamp_from_name,
)

FIXTURE_VIDEO = Path(__file__).parent / "fixtures" / "sample_walkthrough.mp4"

pytestmark_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH"
)
pytestmark_fixture = pytest.mark.skipif(
    not FIXTURE_VIDEO.exists(), reason="tests/fixtures/sample_walkthrough.mp4 not present"
)


def test_frame_name_roundtrip():
    for t in [0.0, 0.5, 14.0, 14.999, 3725.123]:
        name = frame_name_for(t)
        assert frame_timestamp_from_name(name) == pytest.approx(round(t * 1000) / 1000.0)


def test_frame_timestamp_from_name_rejects_non_conforming_name():
    with pytest.raises(ValueError):
        frame_timestamp_from_name("frame_000001.png")


@pytestmark_ffmpeg
@pytestmark_fixture
def test_window_frames_carry_absolute_timestamps(tmp_path):
    frames = extract_frames_window(FIXTURE_VIDEO, tmp_path, start_s=0.0, end_s=4.0, interval_s=1.0)
    assert len(frames) >= 1
    assert all(isinstance(f, ExtractedFrame) for f in frames)
    ts = [f.t_s for f in frames]
    assert ts == sorted(ts)
    for f in frames:
        assert frame_timestamp_from_name(f.path.name) == pytest.approx(f.t_s)


@pytestmark_ffmpeg
@pytestmark_fixture
def test_second_identical_window_reuses_files_without_reinvoking_ffmpeg(tmp_path, monkeypatch):
    extract_frames_window(FIXTURE_VIDEO, tmp_path, start_s=0.0, end_s=4.0, interval_s=1.0)

    calls = []
    original_run = subprocess.run

    def _counting_run(*args, **kwargs):
        calls.append(args)
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _counting_run)
    extract_frames_window(FIXTURE_VIDEO, tmp_path, start_s=0.0, end_s=4.0, interval_s=1.0)
    assert len(calls) == 0


@pytestmark_ffmpeg
@pytestmark_fixture
def test_overlapping_windows_share_frame_files(tmp_path):
    first = extract_frames_window(FIXTURE_VIDEO, tmp_path, start_s=0.0, end_s=6.0, interval_s=1.0)
    second = extract_frames_window(FIXTURE_VIDEO, tmp_path, start_s=3.0, end_s=9.0, interval_s=1.0)

    first_paths = {f.path for f in first}
    second_paths = {f.path for f in second}
    shared = first_paths & second_paths
    assert shared, "overlapping windows should share at least one frame file"
    for p in shared:
        t1 = next(f.t_s for f in first if f.path == p)
        t2 = next(f.t_s for f in second if f.path == p)
        assert t1 == t2


@pytestmark_ffmpeg
@pytestmark_fixture
def test_no_stale_frames_from_a_previous_window(tmp_path):
    first = extract_frames_window(FIXTURE_VIDEO, tmp_path, start_s=0.0, end_s=4.0, interval_s=1.0)
    second = extract_frames_window(FIXTURE_VIDEO, tmp_path, start_s=20.0, end_s=24.0, interval_s=1.0)

    first_ts = {round(f.t_s, 3) for f in first}
    second_ts = {round(f.t_s, 3) for f in second}
    assert not (first_ts & second_ts)


@pytestmark_ffmpeg
@pytestmark_fixture
def test_max_frames_caps_result(tmp_path):
    frames = extract_frames_window(
        FIXTURE_VIDEO, tmp_path, start_s=0.0, end_s=10.0, interval_s=0.5, max_frames=4
    )
    assert len(frames) <= 4


def test_an_empty_or_reversed_window_extracts_nothing(tmp_path):
    assert extract_frames_window(tmp_path / "missing.mp4", tmp_path / "out", 5.0, 5.0) == []
    assert extract_frames_window(tmp_path / "missing.mp4", tmp_path / "out", 8.0, 2.0) == []
