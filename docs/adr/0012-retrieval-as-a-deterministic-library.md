# ADR 0012: Evidence retrieval is a deterministic, budgeted library

**Status:** Accepted; library built and tested, not yet used by the Checker. A model-driven tool loop is an open experiment

## Context

The adaptive-evidence design calls for tools an agent can use to look at the recording: a full frame at a time, a crop, a before/after comparison, an episode summary, a wider interval. ADR 0007 already decided that the Checker's escalation is deterministic because a model that asks for more context tends to over-ask or claim to be done, and because that makes cost unbounded.

## Decision

The tools exist as plain functions (`core/screen/retrieve.py`) and nothing in them calls a model. Every artifact has a content-based id (video fingerprint, kind, time, box, padding, width), so asking for the same thing twice returns the same id with no decode and no charge, on this run or a later one. New artifacts are charged to an `InspectionBudget` (images, crops, expansions, re-inspections of one interval); when it is spent the call returns a recorded `Refusal`. Every call is reported with its latency and whether it was a cache hit. The Checker keeps its two-level deterministic escalation (ADR 0007); its evidence pack now carries the stable ids of what it showed, and they are written to the trace.

Letting a model choose which tool to call is not built. It stays a documented experiment to run against the deterministic path once there is credit and more than one recording.

## Consequences

- Findings and traces can cite exact evidence by id.
- The library is exercised only by tests and the dry run; it is not part of a pipeline run.
- A refusal limits what was looked at. It is never evidence that nothing was there, and the investigation index reports it.

## Rejected alternatives

An agent loop as the default retrieval path (ADR 0007's reasoning, and no measurement shows it helps). Re-decoding the video on every request (cost and latency for no new information). Ids derived from file paths (they would differ between runs of the same recording).
