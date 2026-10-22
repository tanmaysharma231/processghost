"""ADR 0010: changed boxes are merged by proximity, never dropped by size."""
from __future__ import annotations

import pytest

from core.screen.regions import plan_regions, select_regions, split_oversized


def _covers(groups, box) -> bool:
    return any(box in g.members for g in groups)


def test_fewer_boxes_than_cap_are_untouched():
    boxes = [(0, 0, 10, 10), (100, 100, 5, 5)]
    groups = select_regions(boxes, 3)
    assert [g.box for g in groups] == sorted(boxes, key=lambda b: (b[1], b[0]))


def test_tiny_edit_beside_large_change_survives():
    big = [(0, 0, 400, 300), (0, 320, 400, 40), (410, 0, 200, 300)]
    tiny = (900, 700, 12, 8)
    groups = select_regions(big + [tiny], 3)
    assert len(groups) == 3
    assert _covers(groups, tiny)  # an area ranking would have dropped it


def test_every_box_lands_in_exactly_one_group():
    boxes = [(i * 50, (i % 3) * 40, 20, 20) for i in range(9)]
    groups = select_regions(boxes, 2)
    assert len(groups) == 2
    flat = [m for g in groups for m in g.members]
    assert sorted(flat) == sorted(boxes)


def test_nearest_boxes_merge_first():
    boxes = [(0, 0, 10, 10), (12, 0, 10, 10), (500, 500, 10, 10)]
    groups = select_regions(boxes, 2)
    merged = next(g for g in groups if len(g.members) == 2)
    assert (0, 0, 10, 10) in merged.members and (12, 0, 10, 10) in merged.members


def test_cap_must_be_positive():
    with pytest.raises(ValueError):
        select_regions([(0, 0, 1, 1)], 0)


def test_oversized_group_is_split_back_when_cap_allows():
    boxes = [(0, 0, 10, 10), (990, 990, 10, 10)]
    merged = select_regions(boxes, 1)
    assert len(merged) == 1
    out = plan_regions(boxes, 2, 1000, 1000, 0.5)  # cap 2 -> no merge needed
    assert len(out) == 2
    split = split_oversized(merged, 1000, 1000, 0.5)
    assert len(split) == 2 and {g.members[0] for g in split} == set(boxes)


def test_plan_regions_keeps_cap_when_split_would_exceed_it():
    boxes = [(0, 0, 10, 10), (990, 990, 10, 10)]
    out = plan_regions(boxes, 1, 1000, 1000, 0.5)
    assert len(out) == 1 and len(out[0].members) == 2
