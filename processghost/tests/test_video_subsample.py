"""Unit tests for core/io/video.subsample_evenly -- the per-run vision-call cost guard."""
from __future__ import annotations

from pathlib import Path

from core.io.video import subsample_evenly


def _paths(n: int) -> list[Path]:
    return [Path(f"frame_{i:04d}.png") for i in range(n)]


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


def test_a_list_that_already_fits_is_returned_as_is():
    for n in (5, 10):  # under the cap, and exactly at it
        items = _paths(n)
        assert subsample_evenly(items, max_count=10) is items


def test_subsampling_is_evenly_spaced_and_hits_the_cap():
    items = _paths(100)
    assert len(subsample_evenly(items, max_count=20)) == 20
    picked = [items.index(p) for p in subsample_evenly(items, max_count=5)]
    assert picked == [0, 25, 50, 74, 99]  # step 24.75, first and last always kept


def test_result_is_an_ordered_subset_without_duplicates():
    items = _paths(37)
    indices = [items.index(p) for p in subsample_evenly(items, max_count=10)]
    assert indices == sorted(set(indices)) and len(indices) == 10 and indices[0] == 0 and indices[-1] == 36
