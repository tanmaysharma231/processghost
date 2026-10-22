# ADR 0010: Changed regions are merged by proximity, never dropped by size

**Status:** Accepted, built; unproven beyond synthetic tests

## Context

An event can have many changed boxes, and each consumer (the change record the Reconciler reads, the tiles the Checker sees) can show only a few. The first implementation kept the largest boxes by area. Pixel area says nothing about workflow importance: opening a dropdown changes a large area without changing business state, while one edited status cell or amount changes a few hundred pixels. Keeping the largest boxes drops exactly the small edit that matters when it sits beside a big change.

## Decision

When an event has more boxes than a consumer can show, the nearest boxes are merged into groups until the cap holds (`core/screen/regions.py`). Every original box ends up inside some group. A merged group that would cover most of the frame is split back when the cap still allows it. Scroll events keep only what remains after undoing the scroll (`residual_bboxes`), so an edit made during a scroll is not hidden under the "large_shift" label, and an interaction that came and went (a dropdown opened and closed) is kept as a transient event with its peak frame instead of vanishing.

## Consequences

- No box is discarded for being small. A merged crop can be coarser than a tight one.
- Two quick actions in different places can still merge into one event. Splitting them (`SCREEN_SPLIT_ON_LOCUS_SHIFT`) exists but is off by default: it contradicts an existing test that asserts changes inside the settle window are one event, and it has not been measured on a real recording.
- Tested on synthetic clips only (a tiny edit beside a large change, an edit after a scroll, a dropdown opened and closed).

## Rejected alternatives

Rank by changed area and keep the top few (the original behaviour, and a design-document-level rejection of importance-by-magnitude). A learned importance score: no labelled data exists to calibrate one, and it would need the semantic understanding it is meant to save.
