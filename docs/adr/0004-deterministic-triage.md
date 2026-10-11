# ADR 0004: Triage is a pure function with no model call

**Status:** Accepted, built

## Context

Whether an issue blocks a build, warns, or proceeds should be repeatable and auditable.

## Decision

`triage_flag(status, severity)` returns `proceed_with_assumption`, `proceed_with_flag` or `block_and_escalate`. Same document in, same output out. It produces `build_spec.json` and its escalation queue.

## Consequences

- Results can be explained in one sentence and tested exhaustively.
- Severity itself still comes from the Reviewer, so triage is only as consistent as that is.

## Rejected alternatives

An LLM triage step: not repeatable, and nothing to audit.
