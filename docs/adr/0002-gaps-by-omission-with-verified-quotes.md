# ADR 0002: Find structural gaps by omission, grounded in verified quotes

**Status:** Accepted, built; stability still open

## Context

Asking a model "is this step connected to the next?" is biased toward yes. Self-reported confidence is the signal least worth trusting.

## Decision

The Reviewer is asked to name what carries between consecutive steps only when it honestly can, with a verbatim transcript quote. Plain Python checks the quote actually occurs in the transcript. Every consecutive pair without a verified transition becomes a structural flag. There is no ok/broken field to self-report.

## Consequences

- An invented or paraphrased quote counts as silence.
- It does not stop the model from skipping the task entirely: on a fixed draft, zero grounded transitions occurred in 4 of 10 runs, and the structural/ambiguity split moves between runs (`docs/18-known-gaps-v2.md`, `docs/20-lab0-fit.md`).
- A reliability warning in the rendered output tells the reader when a run's structural findings cannot be trusted.

## Rejected alternatives

Special-casing zero transitions: it would silently delete a true finding for a genuinely incoherent explanation. Asking the model to rate each step: self-report.
