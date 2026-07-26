# 08 - Target Pipeline Spec (Python rebuild)

The pipeline contract to build against. Target-state design; no claims here describe existing code.
Builds on `07-target-architecture.md`'s `Run`/`StageExecution` model and provider decision.

## Stage contracts

For each stage: input, output model, model/temperature starting point (carried over from
`workflow_state.json` since those choices were deliberate and reasonable), prompt strategy, failure
mode, retry policy.

### 1. Transcribe

- **Input:** audio track extracted from the uploaded video (`media/audio.py`).
- **Output model:** `Transcript` -- mirrors `response_schemas/transcription_agent_response.json`
  (see `10-migration-map.md` for the field mapping).
- **Model:** Gemini 2.5 Pro equivalent, multimodal, temperature 0.2 (low -- transcription should be
  literal, not creative).
- **Prompt strategy:** system prompt states the task plainly (transcribe, label speakers, timestamp
  turns, note code-switching) with no scenario-specific content baked in -- scenario content
  belongs in test fixtures, not the production prompt (see `06-known-gaps.md` entry 6).
- **Failure mode:** if audio extraction fails (corrupt file, no audio track), the run fails at this
  stage with a typed error; no downstream stage is attempted.
- **Retry:** one automatic retry on transient provider error (5xx/timeout); no retry on a
  successful-but-invalid-schema response -- that is a `ParseError`, surfaced immediately.

### 2. Extract process steps

- **Input:** `Transcript` from stage 1.
- **Output model:** `StepList` -- mirrors `process_extraction_agent_response.json`, with
  `prerequisites: list[str]` and `confidence: float` (fixing the schema-vs-reality mismatch
  recorded in `04-data-contracts.md` section 1.7, where the declared schema said `string` but every
  real example used an array and a number respectively).
- **Model:** Claude, temperature 0.3.
- **Prompt strategy:** extract only explicitly stated steps with evidence quotes and timestamp
  ranges; no synthesis or inference at this stage.
- **Failure/retry:** same pattern as stage 1.

### 3. Detect gaps

- **Input:** `Transcript`, `StepList`, and (from pass 2 onward) the prior pass's `JudgeVerdict
  .findings`.
- **Output model:** `GapList` -- mirrors `gap_detection_agent_response.json`.
- **Model:** Claude, temperature 0.3.
- **Prompt strategy:** describe the categories of expertise blindness to look for (unstated
  prerequisites, vague references, unnamed targets, implied knowledge) generically -- **never**
  supply the specific answer for the specific input scenario the way the as-is demo prompt does
  (`06-known-gaps.md` entry 6, `03-agent-pipeline.md` section 5a). On pass >= 2, explicitly append
  the prior judge findings as additional gaps to investigate, since that is a legitimate and
  intended input (agent 3's own description references "Judge findings on loop iterations").
- **Failure/retry:** same pattern.

### 4. Fill visual gaps

- **Input:** `GapList` (specifically each gap's `timestamp_start`/`timestamp_end`) plus the source
  video file.
- **Output model:** `VisualFillList` -- mirrors `visual_gap_filler_agent_response.json`, with
  `frames_analyzed` populated from the **actual** number of frames extracted and sent, not model
  invention.
- **Model:** a vision-capable model, temperature 0.3.
- **Prompt strategy and media pipeline:** full design in `09-visual-gap-filler.md`. This is the
  fix for `06-known-gaps.md` entries 1 and 4.
- **Failure mode:** if frame extraction fails for a given gap window (e.g. timestamps outside video
  bounds), that gap is marked as visually unrecoverable rather than failing the whole stage --
  partial visual recovery should not block the pipeline, since not every gap is visual in nature
  (the as-is demo data itself only visually resolves 5 of 7 gaps,
  `app/demoData.ts:257-258` `analysis_summary`).
- **Retry:** provider-transient errors retried once; per-gap extraction failures are not retried,
  just recorded.

### 5. Synthesize SOP

- **Input:** `StepList`, `GapList`, `VisualFillList`, `tone`.
- **Output model:** `SopDocument` -- mirrors `documentation_synthesis_agent_response.json`. Every
  step's `added_in_pass` field is populated with the actual current pass number (see "Real loop
  semantics" below -- this field is currently always `1` in the as-is system because the automatic
  loop never runs more than once, `06-known-gaps.md` entry 3).
- **Model:** GPT-family, temperature 0.4, top_p 0.95 (values carried over from
  `workflow_state.json:44-48` as reasonable starting points for a synthesis/writing task).
- **Prompt strategy:** every step must cite its `source` (transcript vs. gap_detection vs.
  visual_fill) and, for non-original steps, its `bridging_explanation`. Steps added in a later pass
  must not silently overwrite steps from an earlier pass without an explicit revision note.
- **Failure/retry:** same pattern.

### 6. Judge

- **Input:** `SopDocument`.
- **Output model:** `JudgeVerdict` -- mirrors `sop_judge_agent_response.json`. `verdict` constrained
  to the literal two values the orchestrator branches on (`APPROVED` / `GAPS_REMAINING`) via an
  enum, not a free string, so a drifted third value fails loudly instead of falling through an
  `else` branch silently (as the as-is code does at `page.tsx:399-403`, where anything other than
  `APPROVED` and `GAPS_REMAINING & pass<max` is treated the same as approval).
- **Model:** Claude, temperature 0.2 (lowest of the six -- judging should be the most consistent
  step).
- **Failure/retry:** same pattern.

## Real loop semantics

The orchestrator (`pipeline/orchestrator.py`) implements the loop `workflow.json` declares but the
as-is code does not execute automatically (`06-known-gaps.md` entry 3):

```
pass = 1
loop:
  run stage 3 (detect_gaps) -- carrying forward findings from previous pass's judge verdict, if any
  run stage 4 (fill_visual_gaps)
  run stage 5 (synthesize) -- steps tagged added_in_pass = pass
  run stage 6 (judge)
  if judge.verdict == APPROVED:
      mark run complete, stop
  elif pass >= max_passes:
      mark run complete-with-open-findings, stop  # explicit terminal state, distinct from APPROVED
  else:
      pass += 1
      continue
```

This runs automatically end to end by default. Whether a human-in-the-loop checkpoint (equivalent
to the as-is "Run Another Pass" button) is offered as an *optional* mode -- e.g. for a "review each
pass before continuing" UX -- is a UI-layer decision deferred to `11-open-questions.md`; the
orchestrator itself must support running unattended, since that is a prerequisite for the
persistence and batch-processing goals in `07-target-architecture.md`.

`max_passes` defaults to 3, matching `MAX_JUDGE_PASSES` (`page.tsx:46`), configurable per run.

The distinction the as-is code blurs (`page.tsx:399-403`, where hitting the pass cap and being
approved both just set `isComplete = true`) is preserved as two different terminal states:
`complete` (judge approved) vs. `complete_with_open_findings` (pass budget exhausted while gaps
remained). Both produce a final `SopDocument`, but only the former should be presented as fully
resolved.

## Context budgeting, replacing truncation

`06-known-gaps.md` entry 5 documents that `safeStringify`/`capMessage` silently drop data (first-10
array items, 2000-char string cap, 15KB/45KB hard limits) with no record of what was cut. Target
behavior:

- Prefer passing **structured references** between stages rather than re-serializing full JSON
  blobs into prompt text where avoidable (e.g. a stage can receive "steps 1-40" as a compact table
  rather than the full nested object from stage 2's raw output).
- Where a transcript or step list is genuinely too large for one call, **chunk** it explicitly
  (e.g. gap detection processing the transcript in ordered windows) rather than truncating and
  discarding the tail silently.
- If truncation of any kind does occur, it must be recorded as a field on the `StageExecution`
  (e.g. `truncated: bool`, `items_dropped: int`) so it is visible in the run record and, ideally,
  surfaced in the final SOP's metadata -- a user should be able to tell that their 40-step process
  was only partially processed, which the as-is system cannot currently tell them at all.

## Prompt management

Prompts move out of inline string literals inside application/orchestration code (as they are in
`app/page.tsx:79-149` and the inline templates in `runPipeline`/`runJudgePass`) and into versioned
files under `prompts/`, one per stage, loaded by `agents/roles.py`. This makes prompt changes
reviewable independently of orchestration logic changes, and makes it possible to run the same
orchestrator against a different prompt version for comparison -- something the as-is single-file
component cannot support at all.

Scenario-specific content (the bills-tracker walkthrough, its known gaps) is explicitly **not**
part of any production prompt (see `06-known-gaps.md` entry 6). It belongs in test fixtures, as
described in `10-migration-map.md`.
