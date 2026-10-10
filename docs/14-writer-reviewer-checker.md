# 14 - The v2 Writer / Reviewer / Checker Architecture

This document describes the v2 pipeline that replaces the two-parallel-token-stream architecture
documented in `13-current-logic-and-architecture.md`. See
`Process_ghost/processghost-v2-writer-reviewer-checker-plan.md` for the original project-owner
spec this implements, and `15-provenance-and-doc-format.md` for the attribution grammar and
provenance rule this document assumes.

## Why the v1 architecture was replaced

The v1 pipeline ran two parallel token streams -- `IntentToken` from the transcript, `ActionToken`
from sampled frames -- and joined them with a keyword + time-proximity aligner
(`core/align/match.py`). That aligner scores an action against an intent using Jaccard keyword
overlap, a hardcoded 7-pair verb-compatibility table, and a linear time decay, thresholded by two
unjustifiable magic numbers (`TAU_LOW=0.25`, `TAU_HIGH=0.65`). It cannot tell a coincidentally
nearby action from one that actually fulfills a narrated claim. The output was a flat list of
`Dissonance` findings -- disconnected fragments, not a document a human could actually follow.

v2 makes the SOP document itself the spine of the pipeline, built by three sequential LLM stages:

**Writer** (transcript -> draft SOP, one LLM call) converts narration into ordered imperative
steps, deliberately naively -- it transcribes what was said, it does not repair, disambiguate, or
resolve anything. If the narration says "paste it here," the step says "paste it here." This is
intentional: a Writer that quietly repairs a gap destroys the signal the next stage exists to find.
See `core/llm/prompts.py: WRITER_SYSTEM` and `core/pipeline.py: _write_sop`.

**Reviewer** (draft SOP -> flagged SOP, one LLM call) reads the draft as a newcomer with zero
process knowledge and does two things in that one call. First, it names what carries between every
consecutive pair of steps -- what specifically exists, is selected, or is true at the boundary that
lets the next step begin -- but only when it can do so honestly, *grounded in a `quote`*: the
verbatim transcript span the claim comes from. `merge_reviewer_output`
(`core/llm/parsing.py: _quote_is_grounded`) verifies that quote is an actual substring of the
transcript before trusting the pair as covered; any consecutive pair left uncovered -- because the
model left it out, or because its `quote` didn't check out -- becomes a synthesized `structural`
flag. This is the actual gap-detection mechanism: absence of a *grounded* answer is the signal,
counted deterministically in code, not a self-reported "is this broken?" judgment. (Omission alone,
with no quote requirement, was tried first and found unstable -- see
`docs/18-known-gaps-v2.md` entry 1.) Second, independently, it runs four ambiguity checks over
every step's instruction (a reference that fails the "could a stranger find this without guessing"
test -- reused from v1's `named_specifically` criterion; missing names; unstated selection rules;
unverified "automatically" claims) and raises an `ambiguity` flag for each hit. Every flag carries
a question phrased for a human, a severity, and a time window for the Checker to look at.

**Checker** (per flag: frames -> resolution, one LLM call per flag) resolves each flag against
video frames extracted densely across its window. Verdicts are `resolved` (the screen shows the
answer), `contradicted` (the screen shows something incompatible with the narration), or
`unresolvable` (nothing in the window settles it) -- and `unresolvable` is framed in the prompt's
own `<context>` as a real, useful outcome, not a dead end: the recording is a sample of someone
explaining a process, and a question the screen can't answer is a genuine question worth taking
back to that person. `CHECKER_SYSTEM` (`core/llm/prompts.py`) is XML-sectioned
(`<role>`/`<context>`/`<scope>`/`<procedure>`/`<verdicts>`/`<output_format>`); its `<scope>` binds
every specific name the model writes to something actually legible in the evidence it was given,
rather than something it could plausibly guess. One call per flag is a deliberate, documented
exception to the batch-everything principle elsewhere in this codebase (see `docs/13` section 10)
-- each flag has its own window, frames, and question, and batching them would muddy every one.
Cost is controlled by a run-level cap and cross-flag frame sharing, not by batching the calls
themselves.

## What's kept from v1

- STT (Whisper / Sarvam Saaras batch) producing timestamped `TranscriptSegment`s -- unchanged.
- The provider abstraction (`.chat()` interface, `config.LLM_PROVIDER` switch), `Metrics`,
  degrade-loudly failure handling, Pydantic data-contract style, and scenario-agnostic prompts.
- The `named_specifically` test, reused verbatim as the Reviewer's ambiguity criterion.
- The grounding scenario and acceptance target (`docs/12-real-scenario-target.md`).

## What's removed

`core/align/` and the six-rule `core/rules/` dissonance engine, subsumed by the Reviewer+Checker,
were deleted once Phase 3 (the Checker) replaced them end-to-end, along with the v1 prompts
(`TRANSCRIPT_EXTRACTION_SYSTEM` through `CROSS_CHECK_SYSTEM`) and `report/render.py` -- they no
longer exist on disk. `core/models/tokens.py` was trimmed down to just `TranscriptSegment` in the
same cleanup, once nothing else importing `Dissonance`/`Evidence`/`RunArtifacts`/`ActionToken`
remained.

## Module layout

The v2 data contract lives in a new module, `core/models/sop.py`, not merged into
`core/models/tokens.py`. Two reasons, both now historical but still the reason the split exists:
`core/rules/`, `core/align/`, and `report/render.py` all imported from `tokens.py` during the v1-to-
v2 transition, so editing it in place would have risked breaking them on every commit before the
replacement was proven; and `Evidence` meant a different thing in each contract (dissonance
evidence in v1, Checker evidence in v2), so one file couldn't hold both without renames that would
just have been undone later. Import direction is one-way: `core/models/sop.py` imports
`TranscriptSegment` from `core/models/tokens.py`; the reverse never happens.

`core/models/tokens.py` is now trimmed to `TranscriptSegment` alone -- the "rewrite tokens.py" the
original plan called for, executed once the v1 code that used to import from it was deleted. There
is only one `RunArtifacts` class (`core.models.sop.RunArtifacts`, the one `run_pipeline` returns);
the legacy `core.models.tokens.RunArtifacts` no longer exists.

## Pipeline shape (current, all four phases)

```
audio extraction (ffmpeg) -> STT -> Writer (1 call) -> provenance-validated SOPDocument ->
sop_draft.md -> Reviewer (1 call) -> flag window/time conversion -> flag selection (severity
order, capped at MAX_CHECKED_FLAGS) -> per-flag windowed frame extraction -> Checker
(1 call per selected flag; Sarvam: one batched OCR job first, then 1 text call per flag) ->
merge resolutions into the doc -> sop.md + build_spec.json
```

The Writer is the pipeline's one FATAL stage (`core/pipeline.py: WriterFailedError`) -- unlike
every other LLM call site in this codebase, it has no per-item fallback, because a run with no
document has nothing for a later stage to work with. On any failure, the run aborts after
recording the failure on the run's metrics. Every later stage degrades instead: a failed Reviewer
call sets `review_failed=True` and ships the Writer's document with zero flags rather than
aborting; a failed Checker call on one flag sets that flag's status to `check_failed` and the run
continues.
