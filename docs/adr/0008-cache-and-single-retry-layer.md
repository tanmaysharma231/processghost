# ADR 0008: Idempotent runs through a content-addressed cache and one retry layer

**Status:** Accepted, built

## Context

The old policy retried every error three times on top of the SDK's own retries, including malformed answers, and its backoff was too short for the rate limits the Checker hit. A rerun repeated every paid call.

## Decision

Every model call is keyed by provider, model, prompts, input, schema, attached image bytes and temperature; a hit costs nothing. Retries are transient-only (rate limit, timeout, connection, 5xx) with jittered exponential backoff, a schema-mismatch is re-asked once, and the SDK's own retries are turned off. Every retry is written to the trace. Temperature is pinned to 0. Each run writes a manifest (code version, prompt hashes, models, thresholds, input hash).

## Consequences

- Verified on a real run: the second identical run cost $0 and produced the same `sop.json`.
- A prompt or config change is a cache miss by design, so experiments stay honest.
- Temperature 0 narrows but does not remove model nondeterminism.

## Rejected alternatives

Caching only at the pipeline level: any change anywhere re-pays everything.
