# Architecture decision records

Short records of the main choices and why. Each states its status honestly: built, planned, or open.

| # | Decision | Status |
|---|---|---|
| 0001 | [The Writer is naive on purpose](0001-naive-writer.md) | Accepted, built |
| 0002 | [Find structural gaps by omission, grounded in verified quotes](0002-gaps-by-omission-with-verified-quotes.md) | Accepted, built; stability still open |
| 0003 | [The Checker observes before it decides, and "unresolvable" is a valid answer](0003-observe-before-verdict.md) | Accepted, built; weak-answer problem open |
| 0004 | [Triage is a pure function with no model call](0004-deterministic-triage.md) | Accepted, built |
| 0005 | [Every line is tagged as said or observed on screen](0005-said-vs-observed-provenance.md) | Accepted, built |
| 0006 | [Build the screen timeline independently of the transcript](0006-independent-screen-timeline.md) | Accepted; timeline built, silent-step value unproven |
| 0007 | [The Checker's escalation is deterministic, never model-requested](0007-deterministic-checker-escalation.md) | Accepted, built |
| 0008 | [Idempotent runs through a content-addressed cache and one retry layer](0008-cache-and-single-retry-layer.md) | Accepted, built |
| 0009 | [Outside context enters as questions, never answers](0009-questions-not-answers-for-outside-context.md) | Accepted design, not built (intake paused) |
| 0010 | [Changed regions are merged by proximity, never dropped by size](0010-regions-merged-never-dropped-by-size.md) | Accepted, built; unproven beyond synthetic tests |
| 0011 | [Three independent triggers feed one investigation index, with coverage accounting](0011-three-triggers-one-investigation-index.md) | Accepted, built; value unproven (one clip) |
| 0012 | [Evidence retrieval is a deterministic, budgeted library](0012-retrieval-as-a-deterministic-library.md) | Accepted; library built, not yet used by the Checker; model-driven loop is an open experiment |
| 0013 | [Surface locators are the context map; speech is corroboration, never a gate](0013-surface-locators-as-the-context-map.md) | Accepted; spreadsheet matched draft labels on one clip, web unproven |
| 0014 | [One flow from what was said and shown, four questions per data step, and an evidence ladder](0014-flow-from-said-and-shown-with-an-evidence-ladder.md) | Accepted, built; model-driven parts not run against a real model |
