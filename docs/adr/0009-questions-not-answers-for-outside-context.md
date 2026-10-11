# ADR 0009: Outside context enters as questions, never answers

**Status:** Accepted design, not built (intake paused)

## Context

A model handed a hypothesis tends to confirm it, and a document-versus-reality contradiction is the most valuable finding.

## Decision

Open questions from a discovery tool or from source documents go only to the Reviewer. The Reviewer may close one only with a verbatim, code-verified transcript quote. A hypothesis counts as confirmed only if a Checker resolution reached without seeing it agrees.

## Consequences

- Not built: the intake prompt does not exist; ingestion of source documents is built.
- It is the intended integration point for a discovery tool.

## Rejected alternatives

Giving the Reviewer the facts to "verify": self-report with extra steps.
