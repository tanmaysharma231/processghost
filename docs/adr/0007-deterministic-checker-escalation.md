# ADR 0007: The Checker's escalation is deterministic, never model-requested

**Status:** Accepted, built

## Context

Letting the model ask for more context is self-report again: models over-ask or claim to be done. It also changes the output schema and makes cost unbounded.

## Decision

Level 0 sends the nearest event as OCR text plus a text model. If the verdict is `unresolvable`, level 1 widens to the events around it and, on OpenAI, attaches images. A flag that is still unresolvable goes back to the recorder. Maximum two calls per flag.

## Consequences

- Cost per flag is bounded; `CheckerOutput` is unchanged.
- A flag whose answer sits further away than two events stays unresolved even if more context would have helped.

## Rejected alternatives

A `needs_more_context` verdict with a direction: smarter targeting but unreliable and unbounded.
