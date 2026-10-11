# ADR 0006: Build the screen timeline independently of the transcript

**Status:** Accepted; timeline built, silent-step value unproven

## Context

If the transcript decides where to look at the screen, a step that is performed but never mentioned is never examined.

## Decision

A plain-Python timeline turns the video into discrete change events (pixel difference, connected components, a settle rule, before/after keyframes at full resolution). The two tracks meet only at Align. The timeline needs no model call.

## Consequences

- Event classification cannot rely on changed-pixel fraction: a spreadsheet sheet switch changed only about 10.8% of pixels (`docs/23-checker-learnings.md`). It uses changed-region coverage plus a pixel floor, and a shift test for scrolls.
- Thresholds were tuned on one clip and are exposed in `core/config.py`.
- On the one clip, Align found no silent step because the Reviewer's flags already covered every event.

## Rejected alternatives

Native video understanding model: most expensive and weakest at small text. Transcript-driven lookup (v2): misses silent steps by construction.
