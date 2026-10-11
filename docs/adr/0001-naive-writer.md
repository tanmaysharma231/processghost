# ADR 0001: The Writer is naive on purpose

**Status:** Accepted, built

## Context

The pipeline exists to find what the expert did not say. A Writer that tidies the transcript into a clean SOP also repairs the vague references ("paste it here"), and the gap disappears before anything can look for it.

## Decision

The Writer keeps the speaker's own words, including pointer words, and never substitutes a more specific name. A prompt rule says so, and an integration test asserts that specifics which are never narrated do not appear in the draft.

## Consequences

- SOP drafts read as vague. That is intended; the later stages and flags add the specifics, tagged by where they came from.
- Any outside context (for example open questions from a discovery tool) must go to the Reviewer, never the Writer.

## Rejected alternatives

Let the Writer infer specifics from context: hides gaps and makes every later stage look better than it is.
