"""Region selection for change records and Checker evidence (ADR 0010).

Why this exists: ranking changed boxes by AREA and keeping the top few silently drops a small, localized
edit (one status cell) that sits next to a large change. Pixel area says nothing about workflow
importance. So when an event has more boxes than a consumer can show, boxes are MERGED by proximity, not
discarded by size: every original box stays inside some group.

Pure functions; no I/O.
"""
from __future__ import annotations

from typing import List, NamedTuple, Sequence, Tuple

from core.models.screen import Box


class RegionGroup(NamedTuple):
    box: Box  # union of the members, original video pixels
    members: Tuple[Box, ...]  # every original box folded into this group


def _union(a: Box, b: Box) -> Box:
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0)


def _gap(a: Box, b: Box) -> float:
    """Distance between two boxes' edges (0 when they touch or overlap), as a Chebyshev-style gap so a
    cell and the formula bar above it are 'near' only by their real separation."""
    dx = max(0, max(a[0], b[0]) - min(a[0] + a[2], b[0] + b[2]))
    dy = max(0, max(a[1], b[1]) - min(a[1] + a[3], b[1] + b[3]))
    return float(max(dx, dy))


def select_regions(boxes: Sequence[Box], cap: int) -> List[RegionGroup]:
    """At most `cap` groups covering ALL of `boxes`. Fewer boxes than the cap are returned untouched
    (one group each); otherwise the two nearest groups are merged repeatedly until the cap holds.
    Output is sorted top-to-bottom, left-to-right by group box."""
    if cap < 1:
        raise ValueError("cap must be >= 1")
    groups: List[RegionGroup] = [RegionGroup(b, (b,)) for b in boxes]
    while len(groups) > cap:
        best = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                key = (_gap(groups[i].box, groups[j].box), _union(groups[i].box, groups[j].box)[2] * _union(groups[i].box, groups[j].box)[3])
                if best is None or key < best[0]:
                    best = (key, i, j)
        _, i, j = best
        merged = RegionGroup(_union(groups[i].box, groups[j].box), groups[i].members + groups[j].members)
        groups = [g for k, g in enumerate(groups) if k not in (i, j)] + [merged]
    return sorted(groups, key=lambda g: (g.box[1], g.box[0]))


def plan_regions(boxes: Sequence[Box], cap: int, frame_w: int, frame_h: int, max_frac: float) -> List[RegionGroup]:
    """select_regions, then split near-full-frame groups back apart while the cap still holds."""
    groups = select_regions(boxes, cap)
    split = split_oversized(groups, frame_w, frame_h, max_frac)
    return split if len(split) <= cap else groups


def split_oversized(
    groups: Sequence[RegionGroup], frame_w: int, frame_h: int, max_frac: float
) -> List[RegionGroup]:
    """A merged group covering more than `max_frac` of the frame is a poor crop (it is nearly the whole
    screen). Split it back into its members' two farthest-apart clusters so each crop stays readable."""
    out: List[RegionGroup] = []
    frame_area = float(frame_w * frame_h)
    for g in groups:
        if len(g.members) < 2 or (g.box[2] * g.box[3]) / frame_area <= max_frac:
            out.append(g)
            continue
        # Seed with the two members that are farthest apart, assign the rest to the nearer seed.
        pairs = [(a, b) for i, a in enumerate(g.members) for b in g.members[i + 1 :]]
        s1, s2 = max(pairs, key=lambda p: _gap(p[0], p[1]))
        c1: List[Box] = [s1]
        c2: List[Box] = [s2]
        for m in g.members:
            if m in (s1, s2):
                continue
            (c1 if _gap(m, s1) <= _gap(m, s2) else c2).append(m)
        for cluster in (c1, c2):
            u = cluster[0]
            for m in cluster[1:]:
                u = _union(u, m)
            out.append(RegionGroup(u, tuple(cluster)))
    return sorted(out, key=lambda g: (g.box[1], g.box[0]))
