# ADR 0005: Every line is tagged as said or observed on screen

**Status:** Accepted, built

## Context

Two kinds of evidence (what the expert said, what the screen showed) mean different things to a reader. Mixing them hides which parts were assumed.

## Decision

Every step carries a `said` attribution with its transcript segments. Anything the Checker adds is a separate `inferred` clause with its frames, never merged into the narrated text. A raising Pydantic validator makes a document without complete provenance impossible to construct.

## Consequences

- The narrated text stays byte-stable across runs.
- A human accepting or rejecting inferred content before it becomes a requirement is not built.

## Rejected alternatives

A soft completeness flag: easy to ignore and to spoof.
