"""Unit tests for core/io/video.subsample_evenly -- the per-run vision-call cost guard."""
from __future__ import annotations

from pathlib import Path

from core.io.video import subsample_evenly


def _paths(n: int) -> list[Path]:
    return [Path(f"frame_{i:04d}.png") for i in range(n)]


def test_returns_unchanged_when_under_cap():
    items = _paths(5)
    assert subsample_evenly(items, max_count=10) == items


def test_returns_unchanged_when_exactly_at_cap():
    items = _paths(10)
    assert subsample_evenly(items, max_count=10) == items


def test_subsamples_down_to_cap():
    items = _paths(100)
    result = subsample_evenly(items, max_count=20)
    assert len(result) <= 20
    assert len(result) > 0


def test_always_keeps_first_and_last():
    items = _paths(50)
    result = subsample_evenly(items, max_count=5)
    assert result[0] == items[0]
    assert result[-1] == items[-1]


def test_max_count_one_keeps_first_only():
    items = _paths(10)
    result = subsample_evenly(items, max_count=1)
    assert result == [items[0]]


def test_non_positive_cap_returns_unchanged():
    items = _paths(10)
    assert subsample_evenly(items, max_count=0) == items
    assert subsample_evenly(items, max_count=-1) == items


def test_result_is_ordered_subset():
    items = _paths(37)
    result = subsample_evenly(items, max_count=10)
    indices = [items.index(p) for p in result]
    assert indices == sorted(indices)
    assert len(set(indices)) == len(indices)  # no duplicates
