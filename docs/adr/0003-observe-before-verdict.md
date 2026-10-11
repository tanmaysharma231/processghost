# ADR 0003: The Checker observes before it decides, and "unresolvable" is a valid answer

**Status:** Accepted, built; weak-answer problem open

## Context

A confident wrong resolution is written into the SOP as if it had been seen. That is worse than an open question.

## Decision

The Checker must write what is literally legible in the evidence first, then decide using only that. It is told repeatedly that `unresolvable` is a real result (the answer lives in the recorder's head). A resolved answer to an ambiguity question must add a concrete detail not in the question. A resolved observation that mostly restates the prompt is downgraded to unresolvable.

## Consequences

- Open questions go back to the recorder instead of becoming guesses.
- Measured on real runs: the model sometimes echoes the prompt, and sometimes resolves with a generic statement. The echo guard handles the first, not the second (`docs/23-checker-learnings.md`).

## Rejected alternatives

A single verdict-plus-answer field: allowed vague but confident resolutions.
