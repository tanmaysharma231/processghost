# ADR 0011: Three independent triggers feed one investigation index, with coverage accounting

**Status:** Accepted, built; value unproven (one clip)

## Context

No single signal covers the recording. Transcript-driven lookup misses actions the speaker never mentions (ADR 0006). Pixel changes alone cannot say which changes matter. A screen-only view cannot tell that a spoken claim was never demonstrated. And when a flag cap or spend budget cuts the work short, the report used to print "Nothing is missing" regardless.

## Decision

Three independent triggers nominate stretches of the recording (`core/screen/investigate.py`):

- **A, context transition:** a screen event that changed application, document, sheet or page, or a scene cut.
- **B, transcript:** a flag raised from what was said, with the screen events attached to it.
- **C, unexplained activity:** a screen event no narrated step accounts for, whether or not anyone was speaking about something else.

Triggers that share an event are merged into one `Investigation` that keeps every reason. A transcript flag with no attached screen change is kept as its own investigation (the transcript asked and nothing on screen was located), not dropped. After the Checker runs, each investigation gets a status: examined, skipped for the flag cap, skipped for the spend budget, or unexamined. The SOP carries a `ScreenCoverage` summary and the report lists the spans not examined, replacing "Nothing is missing" with a caveat whenever coverage is incomplete.

## Consequences

- A cap or budget cut shows up as "not examined", never as an absence of a finding.
- A trigger is a candidate, not a finding. An investigation does not mean a problem.
- Merging is by shared event only, and a transcript flag adds its reason to each event it is attached to instead of bridging them. The first real run showed why: a wide flag window attached nearly every event, and bridging collapsed all investigations into one spanning the whole recording, which made "3 of 3 examined" meaningless.
- Built and unit-tested. On the one real clip, a full `--screen` run (2026-10-18) produced investigations with all three triggers and every one examined; the examined/skipped-by-cap and skipped-by-budget statuses have only been exercised on synthetic data.

## Rejected alternatives

A scalar priority score combining pixel change, transcript relevance and "unexplainedness": change magnitude is a poor proxy for importance, "unexplained" cannot be known before the region is understood (circular), and a number implies precision the variables do not have. Transcript-guided selection alone (ADR 0006). Fixed-rate frames sent to a vision model (kept only as a benchmark).
