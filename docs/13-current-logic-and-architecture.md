# 13 - Current Logic and Architecture (Full Snapshot)

> **Correction (owner, 2026-10-22):** the matching key is the **Transaction String**, not Bill ID. A transaction string is unique because it carries a specific name and number; Bill ID is generated per bill for individual products, so it has duplicates (the same id recurs for different use cases, and across months in Monthly Actuals). Read every 'Bill ID' below as 'Transaction String' where it names the join key. The evaluation files in `processghost/evals/cases/bills_tracker/` carry the corrected key.

> **Second correction (owner, 2026-10-22):** setting Status to "Paid" is **not** in the recording. It is a rule of the workbook (the total only counts rows whose Status is "Paid"), so the pipeline should raise it as a question, not state it as a step of the process. Also, the workbook open in the video is not identical to `ProcessGhost_Bills_Tracker_v4_final.xlsx`: the video has a **Total billed** row (27) and a **Total Actualized** row (28); the xlsx has one total row. The pipeline now checks that a supplied workbook matches the recording (`core/data/match.py`).

> **Superseded.** This is a snapshot of the **v1** architecture (parallel intent/action token
> streams, a keyword+time-proximity aligner, a six-rule dissonance engine) -- kept as the as-is
> reference `docs/14-writer-reviewer-checker.md` explains the departure from, not as a description
> of the current pipeline. `core/align/` and `core/rules/`, both described below as current, were
> deleted once the Checker (v2 Phase 3) replaced them end-to-end; they no longer exist on disk. For
> the actual current pipeline (Writer / Reviewer / Checker / Triage), read `docs/14` through
> `docs/18-known-gaps-v2.md` instead.

This was, at the time it was written, a complete, self-contained explanation of ProcessGhost as it
stood: the idea, why it exists, the concrete video/workbook scenario it's built and tested against,
the end-to-end pipeline, every prompt the LLM is given, the alignment/rule logic that turns model
output into findings, and the provider migration in progress. Written so someone with zero prior
context on this repo could understand the whole system from this file alone -- true of the v1
system it describes, not of the repo as it exists now.

---

## 1. The idea

An expert records their screen while narrating a business process (e.g. "how I update the bills
tracker each month"). The recording is meant to become documentation (an SOP) for someone else to
follow later. The problem: **what the expert says out loud is never the complete process.**
Experts skip steps that are obvious to them but invisible to a newcomer -- which exact sheet they
mean by a vague name, which column a value goes into, what condition actually has to be true for
some "automatic" thing to happen, which of several similar-looking rows is the right one.

ProcessGhost's job is to **find the gap between narration and screen action** and surface exactly
what's missing, so the recording can be turned into documentation that actually works for someone
who doesn't already know the process. It is not a transcription tool and not a screen-recorder --
both narration and screen are already given as input; ProcessGhost's value is entirely in the
cross-referencing between them.

---

## 2. The concrete grounding scenario

Everything in this repo is built and acceptance-tested against one real input pair, not a made-up
example (see `docs/12-real-scenario-target.md`):

- **Video**: `data/english_hackathon_test.mp4` -- 23.9s, 1920x1080, with audio. Real transcript:

  > "So we have a master files here, what we do is we go to monthly actions, based on what value we
  > have, we fill the actualization, taking the data from transactions. So Amazon Web Services, I
  > copy it, go to monthly actions, I paste it here, and the total will get updated automatically."

- **Workbook**: `data/ProcessGhost_Bills_Tracker_v4_final.xlsx`, three tabs:
  1. **Master Bills** -- 8 rows: Bill ID, Vendor Name, Category, Expected Amount, Due Date, Notes.
  2. **Monthly Actuals** -- Month, Bill ID, Transaction String, Category, Expected (E), **Actual
     (F)**, Variance (G, live formula `=IF(F<>"",F-E,"")`), **Status (H)**, Entered By, Remarks.
     March 2025 rows (18-25) are empty in Actual and Status -- that's the work being done on
     camera. Row 27 totals March: `F27=SUMIFS(F18:F25,A18:A25,"Mar 2025",H18:H25,"Paid")` --
     **only counts a row if Status is exactly "Paid."**
  3. **Transactions - March** -- Bill ID, Transaction String, Actualized Amount. Transaction
     strings are garbled bank text (e.g. `AMZN WEB SVCS IN *AWS53871` for AWS), not clean vendor
     names.

### What the narration says (explicit, in order)
1. References "master files" (a reference sheet exists).
2. Goes to "monthly actions" (navigates to the working sheet).
3. Determines "what value we have."
4. Says the value comes "from transactions."
5. Names "Amazon Web Services" and copies its value.
6. Goes back to "monthly actions" and pastes it.
7. Claims the total "will get updated automatically."

### What's missing (never said, but real and necessary)
1. **Sheet identity x3** -- "master files", "monthly actions", "transactions" are never named
   precisely. They are **Master Bills**, **Monthly Actuals**, **Transactions - March**.
2. **Row-matching logic** -- never explained. The real join key is **Bill ID**
   (`BILL-001`), not the vendor name -- the transaction string (`AMZN WEB SVCS IN *AWS53871`)
   would never match "Amazon Web Services" by name. This is the single most important gap.
3. **Paste destination** -- "I paste it here" names no column. It's specifically **Column F
   ("Actual")**.
4. **Status requirement** -- the claim "updates automatically" is only true if **Status (Column
   H) is set to exactly "Paid"**; this is never mentioned, and a real pipeline run's frame analysis
   found edits to Column F but never observed a Status edit -- flagged as an unverified/at-risk
   claim, not silently assumed true.
5. **Formula protection** -- Column G (Variance) is a live formula and must never be manually
   overwritten; not directly at risk in this recording but a standing hazard worth a warning.

This table (gap -> exact bridging fact) is `docs/12-real-scenario-target.md` section 3, and is the
acceptance target for the not-yet-built SOP-synthesis stage: given this exact input, the output is
only correct if it independently surfaces all of the above without them being hand-fed to the
model. The prompts are deliberately scenario-agnostic (no sheet names, vendor names, or column
letters baked in) specifically so this scenario functions as a real test of whether the system can
*find* the answer, not recite it (see `docs/06-known-gaps.md` entry 6).

---

## 3. Repo layout

```
Process_ghost/                                   (git repo)
  docs/                                           design docs, 00-README onward
  data/                                           the real video + workbook above
  processghost/                                   the actual working Python implementation
```

Inside `processghost/`:
```
core/
  config.py            central defaults + the LLM_PROVIDER switch
  pipeline.py           run_pipeline() -- the orchestrator, see section 5
  io/
    video.py             ffmpeg audio extraction, frame extraction, subsampling
    images.py            frame -> base64 data URI (for OpenAI vision)
    storage.py            per-run directory management
  llm/
    client.py            OpenAI wrapper (Whisper/gpt-4o/gpt-4o-mini) + Metrics + persistence
    sarvam_client.py      Sarvam AI wrapper (STT/chat/OCR/TTS), see section 6
    prompts.py            all LLM system prompts, see section 4
    parsing.py            turns raw model/API responses into typed tokens, with validation
  align/
    aligner.py            build_alignments() -- links each action to its best-matching intent
    match.py               the actual scoring function aligner.py calls
  models/
    tokens.py              all Pydantic schemas (the data contract end to end)
  rules/
    engine.py               runs the rule list, appends findings to artifacts.dissonances
    r_unexplained.py        action with no matching narration
    r_silent_step.py        very little narration but several actions occurred
    r_teleportation.py      terminal action (export/submit/approve) with no prerequisite steps
    r_contradiction.py      negation words present near actions (placeholder heuristic)
    r_unresolved_reference.py   wraps the cross-check call's unresolved-entity findings
    r_unconfirmed_outcome.py    wraps the cross-check call's unconfirmed-claim findings
report/
  render.py               writes the markdown/HTML report from RunArtifacts
tests/
  test_pipeline_fallback.py, test_parsing.py, test_integration_real_video*.py
```

---

## 4. Pipeline, step by step

`core/pipeline.py: run_pipeline(video_file)` is the entire orchestration. Steps, in order:

1. **Setup** -- new run directory, copy the input video in, instantiate a `Metrics` tracker and
   the active provider's client (`_make_client`, branches once on `config.LLM_PROVIDER`).

2. **Audio extraction** -- `core/io/video.extract_audio` pulls a `.wav` out of the video via
   ffmpeg.

3. **Transcription (STT)** -- provider-specific call, but both converge on the same shape
   (`list[{"id","start","end","text"}]`) so everything downstream is provider-agnostic:
   - OpenAI: Whisper (`whisper-1`).
   - Sarvam: a Saaras v3 **batch job** (create -> upload -> start -> wait -> fetch results),
     parsed by `parse_sarvam_stt_result`.

4. **Intent extraction -- ONE LLM call for the whole transcript.**
   `_extract_intents_from_transcript` sends every transcript segment at once (indexed, with
   timestamps) to `TRANSCRIPT_EXTRACTION_SYSTEM` (section 5.1 below) and parses back one
   `IntentToken` per segment, each carrying:
   - `intent`/`verb`/`obj` -- what the speaker says they're doing.
   - `entities_referenced` -- every thing mentioned (file/sheet/column/value/status/person),
     each tagged with a role and whether it was named *specifically enough to find*.
   - `selection_criteria` -- if stated, how to pick the right item among several.
   - `claimed_outcome` -- if stated, what the speaker asserts will happen as a result.

   On any failure (call error, malformed/wrong-length response), the **whole batch** falls back to
   a keyword heuristic (`_intent_heuristic`: flags "check"/"verify"/"review"/"make sure" as vague)
   and the failure is recorded on `Metrics`, never silently swallowed.

5. **Frame extraction** -- `video.extract_frames` samples frames at a fixed interval
   (`config.FRAME_INTERVAL_S`, default 2.0s). Only a capped subset actually gets a real
   description call (`MAX_VISION_CALLS_PER_RUN` / `MAX_OCR_FRAMES_PER_RUN`, both 20) via even
   subsampling -- the rest get a stub `ActionToken` (`action_type="unknown"`, low confidence).
   This budget exists purely for cost/latency; see `docs/09-visual-gap-filler.md` for the
   scene-change-based sampling this is a placeholder for.

6. **Action description (per sampled frame)** -- provider-specific:
   - **OpenAI path** (`_action_from_frame`): true vision call, frame sent as a base64 data URI
     (downscaled to `VISION_MAX_DIMENSION=1024px` on the long side -- full-res 1920x1080 frames
     were measured at ~37k prompt tokens each, ~90% of that run's total cost) to `gpt-4o-mini`
     with `ACTION_DESCRIPTION_SYSTEM`.
   - **Sarvam path** (`_build_actions_sarvam` + `_action_from_ocr_text`): Sarvam has **no general
     vision model** (Sarvam-105B is text-only chat; "Sarvam Vision" is document/OCR extraction
     only), so this is a two-step OCR-then-reason substitute:
     1. One **batched** document-intelligence job over *all* sampled frames at once (zipped
        together, one job for the whole run -- job-based flows have real per-job latency, so one
        job per frame would be both slow and wasteful).
     2. For each frame's OCR'd text/markdown, a text-only chat call
        (`ACTION_INFERENCE_FROM_TEXT_SYSTEM`) infers the likely action -- explicitly instructed to
        calibrate confidence lower than true vision would, since cursor position, highlighting,
        color, and layout are all invisible from OCR text alone.
   - Both produce the same `ActionToken` shape (`action_type`, `target`, `details`, `app`,
     `confidence`). Any single-frame failure falls back to a stub for that frame only and records a
     `Metrics` failure; a whole-batch OCR failure degrades every frame in that batch to stubs.

7. **Alignment** -- `build_alignments` (deterministic, no LLM): for every action, scores it
   against every intent via `core/align/match.py`'s `match_score` (keyword + time-proximity, not
   semantic) and keeps the best match. Below `TAU_LOW` (0.25) -> unexplained (no intent). Between
   `TAU_LOW` and `TAU_HIGH` (0.65) -> uncertain match. Above `TAU_HIGH` -> explained.

8. **Cross-check -- ONE LLM call, after both intents and actions are known.**
   `_cross_check_resolution` sends a compact summary of everything said (entities referenced,
   selection criteria, claimed outcomes) and everything observed (action type/target/details per
   sampled frame) to `CROSS_CHECK_SYSTEM` (section 5.2) in a single call, and gets back two lists:
   - `unresolved_entities` -- a reference that was never named specifically AND that nothing
     observed on screen ever resolves, anywhere in the run.
   - `unconfirmed_claims` -- a claimed outcome that no observed action plausibly confirms.
   This is deliberately whole-run, not pairwise, because only a model that can see the *entire*
   transcript and *entire* action list at once can catch "this was referenced three times and
   never resolved" or "this was claimed once and nothing ever confirmed it" -- a narrower
   per-fragment call structurally cannot do that cross-referencing. On failure: records one
   `Metrics` failure, returns two empty lists (the rules that consume them just produce no findings
   rather than crashing the run).

9. **Rule engine** -- `core/rules/engine.run_rules` runs a fixed list of rules over the
   `RunArtifacts`, each appending zero or more `Dissonance` findings (section 6).

10. **Report + persistence** -- `report/render.write_reports` produces markdown/HTML; run metrics
    (`Metrics.to_dict()` + wall-clock duration) and the full `RunArtifacts` JSON are persisted to
    disk (`persist_metrics`, `persist_run_json`).

### Why intent extraction and cross-check are each exactly one call
This is a deliberate, repeatedly-stated design principle (module docstrings in `pipeline.py` and
`prompts.py`, and [[feedback-batched-llm-calls]]): giving the model the *whole* transcript, or the
*whole* set of extracted entities/claims plus a compact summary of observed actions, in a single
call lets it cross-reference across the entire recording -- something many small per-segment or
per-pair calls cannot structurally do -- at lower total cost than the many-small-calls alternative.

---

## 5. The prompts (what the LLM is actually told)

All three are intentionally **scenario-agnostic** -- no sheet name, vendor, or column is baked in,
so the real scenario in section 2 functions as a genuine test of whether the model can find the
answer rather than being handed it (`docs/06-known-gaps.md` #6; [[feedback-generalize-not-overfit]]).

### 5.1 `TRANSCRIPT_EXTRACTION_SYSTEM` (one call, whole transcript)
Given every transcript segment at once (indexed, with timestamps), for each segment extract:
- the primary **intent** (short paraphrase, verb, object, confidence),
- **entities_referenced**: every distinct thing mentioned, each with `raw_mention`, an
  `entity_role` (`location_or_container` / `target_field` / `data_value` / `condition_or_status` /
  `actor` / `other`), and `named_specifically` (true only if someone unfamiliar with the process
  could find it without guessing -- a bare "here"/"this one"/unclarified pronoun is false),
- **selection_criteria**: how to pick the right item among several, if stated (never inferred),
- **claimed_outcome**: what the segment asserts will/has happened, especially anything described
  as automatic or a consequence of another step.

Because the model sees the whole transcript at once, it's explicitly told it may use a *later*
segment to note that an earlier vague reference is eventually clarified -- but a reference must
still be flagged vague if it's *never* clarified anywhere in the transcript. Segments with no
actionable content (small talk, pauses) get empty/null fields and low confidence rather than
forced content.

### 5.2 `ACTION_DESCRIPTION_SYSTEM` (OpenAI vision path, per sampled frame)
Given one frame, no audio, no surrounding frames: identify the application/view, the specific UI
element in focus if any, and what the frame suggests the user just did or is about to do. Returns
`action_type` (snake_case label, e.g. `edit_value`, `filter`, `export`, or `unknown`), `target`,
`details` (one sentence), `app`, `confidence`. Blank/transitional frames get `unknown` + low
confidence rather than a guessed action.

### 5.3 `ACTION_INFERENCE_FROM_TEXT_SYSTEM` (Sarvam path, per sampled frame, text-only)
Same output shape as 5.2, but explicitly told it is working from OCR-extracted text/table structure
only -- no cursor, no highlighting, no color, no layout -- and must calibrate confidence lower and
be explicit in `details` when something is a guess made without seeing the actual screen, rather
than asserting a specific action with unwarranted certainty.

### 5.4 `CROSS_CHECK_SYSTEM` (one call, after intents + actions both exist)
Given `narration_items` (id, entities referenced + whether each was named specifically, selection
criteria, claimed outcome) and `observed_actions` (id, timestamp, action_type/target/details): for
every entity that was *not* named specifically, check whether *any* observed action anywhere
resolves what it refers to; if nothing does, it's unresolved. For every claimed outcome, check
whether any observed action *plausibly* confirms it happened (explicitly: an unrelated action
merely nearby in time does not count as confirmation). Only genuine gaps are reported -- the model
is told not to flag anything clearly resolved/confirmed. Returns `unresolved_entities` and
`unconfirmed_claims` arrays (each may be empty).

---

## 6. The dissonance (finding) rules

Six rules run over the fully-populated `RunArtifacts`; each independently appends `Dissonance`
records (id, kind, severity, timestamp, title, explanation, linked action/intent ids, evidence,
rule id/version):

| Rule | Logic | Source |
|---|---|---|
| `r_unexplained` | An action has no matching intent (`alignment.intent_id is None`, score below `TAU_LOW`) | deterministic alignment |
| `r_silent_step` | Transcript is very short (<40 chars) but >=3 actions occurred | naive heuristic |
| `r_teleportation` | A terminal action (export/submit/approve) occurs with no prior prerequisite action (open/filter/sort/edit) anywhere earlier in the run | deterministic |
| `r_contradiction` | Negation/absolute words ("never", "not", "only", ...) appear anywhere in the transcript and actions exist -- placeholder, flags for manual review | keyword heuristic |
| `r_unresolved_reference` | Thin wrapper over `artifacts.unresolved_entities` from the cross-check call | LLM (5.4) |
| `r_unconfirmed_outcome` | Thin wrapper over `artifacts.unconfirmed_claims` from the cross-check call | LLM (5.4) |

`r_unresolved_reference` replaced an older `r_vague_intent` that fired on an arbitrary "matched to
>=3 actions" threshold -- the current version is a strictly more precise, non-scenario-specific
signal driven by the model's whole-run semantic judgment rather than a magic-number heuristic.
`r_unconfirmed_outcome` has no deterministic equivalent at all: a keyword+time-proximity aligner
cannot tell a coincidentally-nearby action from one that actually fulfills a specific claim, which
is exactly why it needs the cross-check call's semantic judgment.

`r_contradiction` and `r_silent_step` are explicitly lightweight/naive placeholders, not final
logic.

---

## 7. Data contract (`core/models/tokens.py`)

Everything is a Pydantic v2 model, forming one continuous pipe from raw input to final report:

- `TranscriptSegment` (start/end/text/speaker)
- `EntityReference` (raw_mention, entity_role, named_specifically)
- `IntentToken` (id, timing, raw_text, intent/verb/obj, confidence, is_vague, entities_referenced,
  selection_criteria, claimed_outcome)
- `ActionToken` (id, timing, frame_path, action_type/target/details/app, confidence, roi)
- `Alignment` (action_id -> intent_id, match_score, reason, notes)
- `Evidence` (timestamp, frame paths, transcript excerpt/range, extra)
- `Dissonance` (id, kind, severity, timestamp, title, explanation, linked ids, evidence, rule
  id/version)
- `UnresolvedEntityFinding` / `UnconfirmedClaimFinding` (the cross-check call's two output types)
- `RunArtifacts` -- the top-level container threaded through the whole pipeline: run_id, paths,
  transcript, intents, actions, alignments, unresolved_entities, unconfirmed_claims, dissonances,
  report paths, metrics.

Every LLM prompt's expected JSON shape is written to match these models field-for-field so parsing
(`core/llm/parsing.py`) can construct them directly from a response.

---

## 8. Failure handling philosophy

Every LLM call site follows the same shape: try the real call -> on **any** exception or
unparseable/malformed response, **record the failure on `Metrics`** (`Metrics.record_failure`,
with a stage name and metadata) and **degrade to a fallback** rather than crashing the run:
- Intent extraction fails -> whole batch falls back to the keyword heuristic.
- A single frame's action description fails -> that frame gets a stub `ActionToken`
  (`action_type="unknown"`, confidence 0.3).
- The Sarvam OCR batch job fails -> the *whole batch* of frames degrades to stubs (same
  whole-batch-degrades philosophy as intent extraction).
- Cross-check fails -> both finding lists come back empty; the two rules that consume them simply
  produce no findings for that run.

Nothing fails silently -- every degradation is recorded on the run's metrics so a run can always be
audited afterward for where and why it degraded. See `docs/08-target-pipeline-spec.md` for the
fuller rationale.

---

## 9. Provider migration: OpenAI -> Sarvam AI (in progress)

The pipeline originally ran 100% on OpenAI (Whisper for STT, gpt-4o for text, gpt-4o-mini for
vision). The user asked to move the entire process to Sarvam AI. `config.LLM_PROVIDER` is a single
switch (`"openai"` or `"sarvam"`, currently defaulted to `"sarvam"`) and both providers stay
selectable; `pipeline.py`'s `_make_client` is the only place construction branches on it -- every
downstream call site works unchanged because both clients expose the same `.chat(messages, **kwargs)
-> dict` / `.metrics` interface.

**Key finding that shaped the whole Sarvam path**: Sarvam has **no general vision model**.
Sarvam-105B chat is text-only; "Sarvam Vision" is document/OCR-extraction only, not an
image-understanding model. The design response was OCR-extract-then-text-reason instead of true
vision (section 4, step 6) -- an explicitly accepted capability regression versus true vision,
chosen deliberately by the user rather than discovered as a limitation later.

Model mapping:
| Stage | OpenAI | Sarvam |
|---|---|---|
| STT | `whisper-1` | `saaras:v3` (batch job) |
| Chat/reasoning | `gpt-4o` | `sarvam-105b` |
| Vision/action description | `gpt-4o-mini` (true vision) | document-intelligence OCR + `sarvam-105b` text reasoning |
| TTS (planned, not yet wired into the pipeline) | -- | `bulbul:v3`, speaker `shubh`, `en-IN` |

`core/llm/sarvam_client.py` was written against the installed `sarvamai` SDK (v0.1.28) by **direct
introspection**, not by trusting `docs.sarvam.ai` -- the web docs were frequently stale or 404
during research, and at least one docs summary (claiming `response_format` support on chat
completions) turned out to be wrong against the actual SDK signature (see
[[reference-sarvam-ai-docs]]). What's confirmed vs. still best-effort:

- **Confirmed by SDK introspection**: chat completions signature and response shape (matches
  OpenAI's shape closely enough that `extract_chat_content()` needs no changes); no
  `response_format` param exists (JSON is enforced by prompt instruction only, same defensive
  parsing the rest of the codebase already assumes); the STT batch-job lifecycle
  (`create_job -> upload_files -> start -> wait_until_complete -> get_file_results`); the
  document-intelligence job lifecycle (`create_job -> upload_file` [singular -- hence one ZIP
  bundling every sampled frame] `-> start -> wait_until_complete -> download_output`); TTS
  response shape (`.audios: List[str]`, base64 WAV).
- **NOT yet verified against a real API response** (no `SARVAM_API_KEY` was available while
  writing this): the exact internal structure of the STT result dict's `timestamps` field, and
  critically the **document-intelligence output ZIP's internal structure** -- which file holds
  page-level JSON and how pages map back to original per-frame filenames. `_parse_ocr_output_zip`
  is a best-effort parse (tries a couple of reasonable key names, falls back to positional mapping
  by sorted filename) that raises loudly with the ZIP's file listing if nothing matches, rather
  than silently returning wrong data.
- `tests/test_integration_real_video_sarvam.py` is gated on `SARVAM_API_KEY` and will be the first
  real-response check of all of the above once a key exists.

**Next planned step after the provider swap lands**: a Streamlit page with a preloaded video and a
chatbot that speaks the missing-steps answer aloud via Sarvam's Bulbul TTS.

---

## 10. Design principles this build follows (why, not just what)

- **Batch, don't fragment, LLM calls.** One whole-transcript call, one whole-run cross-check call,
  one whole-batch OCR job -- not many small per-item calls. Cheaper and structurally lets the model
  cross-reference across the entire run. See [[feedback-batched-llm-calls]].
- **Generalize, don't overfit to the one grounding scenario.** Prompts contain no sheet name,
  vendor, or column -- the real scenario (section 2) is a test of whether the system finds the
  answer, not a template it's allowed to memorize. See [[feedback-generalize-not-overfit]].
- **Degrade loudly, never silently.** Every fallback is recorded on `Metrics`; nothing masks a
  failure as a clean result.
- **Prefer whole-run semantic judgment over threshold heuristics** where the signal genuinely needs
  it (unresolved references, unconfirmed outcomes) -- keyword/proximity heuristics remain only
  where they're good enough (`r_teleportation`, `r_silent_step`) or as an explicit fallback path.
