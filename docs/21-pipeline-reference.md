# 21 - Pipeline Reference

One place that describes the whole run: every stage, what goes in, what comes out, what happens
when it fails, and the prompt that drives it. The prompts themselves are copied verbatim in the
appendix from `processghost/core/llm/prompts.py` at the time of writing. If the two ever disagree,
`prompts.py` wins -- regenerate the appendix rather than editing it by hand.

Design rationale lives elsewhere: `14-writer-reviewer-checker.md` (why this chain),
`15-provenance-and-doc-format.md` (the attribution grammar), `16-prompt-guide.md` (prompt lessons),
`18-known-gaps-v2.md` (defects). This doc is the "what runs, in what order" view.

## The run at a glance

```
video.mp4
  |  ffmpeg: extract audio
  v
audio.wav
  |  STT (OpenAI Whisper, or Sarvam Saaras v3)
  v
transcript segments [{id, start, end, text}]
  |
  v
[1 WRITER]   LLM, one call, whole transcript      -> SOPDocument (steps + appendix)   FATAL on failure
  |
  v
[2 REVIEWER] LLM, one call, steps + transcript    -> transitions + flags              non-fatal
  |            then merge_reviewer_output (plain Python): coverage check adds structural flags
  v
[3 CHECKER]  LLM, one call PER FLAG, frames/OCR   -> verdict per flag                 non-fatal per flag
  |
  v
[4 TRIAGE]   plain Python, no LLM                 -> per-flag triage + escalation queue
  |
  v
sop_draft.md (after Reviewer), sop.md, sop.json, build_spec.json, trace.jsonl, run.json
```

Orchestrator: `core/pipeline.py: run_pipeline`. Every LLM stage goes through
`structured_chat(system_prompt, user_text, schema, ...)`, which validates the reply against a
Pydantic schema (`core/models/sop.py`). Both providers (OpenAI via LangChain, Sarvam via
JSON-mode-by-prompt) expose that same interface, so no stage branches on provider at the call site
except where the Checker needs vision vs OCR.

## Stage 0 - Transcription (not an agent)

- In: `audio.wav` extracted from the video.
- Out: `segments_raw`, a list of `{id, start, end, text}`.
- Provider: Whisper (`whisper-1`) or Sarvam Saaras v3. Priced per minute of audio.
- Degraded case: if STT returns one whole-transcript segment with no timing, the run records
  `stt_timing_degraded` and sets `timing_degraded` on the document so the output says its
  timestamps are unreliable instead of showing fake `@0:00` attributions.

## Stage 1 - Writer

- **Job:** turn the transcript into ordered, imperative steps, exactly as narrated.
- **In:** every transcript segment at once (index, start, end, text).
- **Out:** `WriterOutput` -> `SOPDocument` with `steps` (title, instruction, `said` attributions
  listing the source segment ids) and `appendix_segments` (small talk and filler, each with a reason).
- **Key rule:** deliberately naive. It keeps the speaker's vague wording ("paste it here") and never
  substitutes a specific name. A repaired step would erase the gap the later stages exist to find.
- **Code-enforced:** every segment accounted for exactly once; every step has at least one `said`
  attribution (`build_sop_from_writer_output`, `core/llm/parsing.py`).
- **Failure:** FATAL. Any failure raises `WriterFailedError` and aborts the run, because there is no
  document to fall back to. The failure is recorded on the run's metrics first.
- **Model:** `config.MODEL_TEXT` (gpt-4o) or `sarvam-105b`.

## Stage 2 - Reviewer

- **Job:** read the draft as a newcomer and find every place they would get stuck.
- **In:** the ordered steps plus the full transcript.
- **Out:** `ReviewerOutput` with two lists:
  - `transitions`: for each pair of consecutive steps, the specific state that carries across,
    with a verbatim transcript `quote`. Emitted only when the model can honestly name it.
  - `flags`: ambiguity flags (from four checks: unresolved pointer words, missing names, unstated
    selection rules, unverified consequences) and structural flags. Each has a question, a severity
    (`blocking` / `caution` / `note`) and a window of transcript segment indices.
- **Gap detection by omission:** there is no "ok/broken" field for the model to self-report. After
  the call, `merge_reviewer_output` (plain Python) walks every consecutive step pair and creates a
  structural flag for any pair without a transition whose quote actually occurs in the transcript.
  An empty, ungrounded or off-chain transition counts as no coverage.
- **Failure:** non-fatal. On any failure the Writer's document ships unchanged with
  `review_failed=True` and zero flags, and the rendered output shows a "REVIEW FAILED" banner.
- **Known issue:** the split between structural and ambiguity flags varies run to run on identical
  input; see `18-known-gaps-v2.md` entry 1.

## Stage 3 - Checker

- **Job:** resolve ONE flag against what was actually on screen.
- **Selection:** open flags sorted blocking-first; the first `MAX_CHECKED_FLAGS` (20) are checked,
  the rest are marked `unchecked` (never silently dropped).
- **Evidence per flag:** frames extracted from the flag's time window
  (`extract_frames_window`: window from the flag's segments, `CHECKER_LEAD_IN_S` lead-in,
  `CHECKER_MIN_WINDOW_S` minimum, one frame per `CHECKER_FRAME_INTERVAL_S`, at most
  `CHECKER_MAX_FRAMES_PER_FLAG`).
  - OpenAI path: the frames go to a vision model with the call.
  - Sarvam path (no vision model): all checked flags' frames are OCR'd in one batched job, then each
    flag is reasoned over its extracted text.
- **In (per call):** the flag's question and family, the relevant step excerpt, the transcript
  excerpt for the window, plus frames or OCR text.
- **Out:** `CheckerOutput`: `observed` (written first), `verdict`, and depending on verdict
  `inferred_text`, `reason`, or `narration_version` / `screen_version`.
- **Verdicts:**
  - `resolved`: ambiguity flags get an `InferredClause` appended to their step (narrated text stays
    byte-stable); structural flags get a new inferred step inserted between the two bounding steps.
  - `contradicted`: the flag is forced to `blocking`.
  - `unresolvable`: a real terminal status meaning the answer lives only in the recorder's head.
- **Key rule:** write down what is literally legible before deciding, and "unresolvable" is always
  an acceptable answer. A fabricated resolution is worse than an open question.
- **Failure:** non-fatal per flag. The flag becomes `check_failed` and the run continues. Frame
  extraction and the OCR batch also fail without aborting the run.
- **Cost note:** this stage dominates per-run cost (about $0.04-0.06 per flag on OpenAI vision).
  The frame-based approach is under review (see the plan file); treat this stage as provisional.
- **Model:** `config.MODEL_VISION` (gpt-4o-mini) or `sarvam-105b` over OCR text.

## Stage 4 - Triage (no LLM, no prompt)

`report/build_spec.py: triage_flag`, a pure function of a flag's status and severity:

| Flag status / severity | Triage |
|---|---|
| `resolved` (any severity) | `proceed_with_assumption` |
| `contradicted` | `block_and_escalate` |
| anything else (`open`, `unresolvable`, `unchecked`, `check_failed`) with severity `blocking` | `block_and_escalate` |
| anything else with severity `caution` or `note` | `proceed_with_flag` |

`build_spec()` adds the steps, the flags with their triage, and an `escalation_queue` (everything
that is `block_and_escalate`, severity-sorted). Same document in, same output out.

## Outputs of a run

All under `runs/<run_id>/`:

| File | What it is |
|---|---|
| `sop_draft.md` | Rendered right after the Reviewer, before any Checker work |
| `sop.md` | Final human-readable SOP with provenance tags and flag sections |
| `sop.json` | The validated `SOPDocument` |
| `build_spec.json` | Steps, flags with triage verdicts, escalation queue |
| `trace.jsonl` | One line per LLM call, payload and failure, written as they happen |
| `run.json` | Full artifacts plus metrics (tokens, cost, calls, fallbacks) |
| `checker_frames/` | Frames extracted for the Checker |

## Failure behavior summary

| Stage | On failure | Visible as |
|---|---|---|
| Writer | Run aborts (`WriterFailedError`) | Exception; failure in metrics |
| Reviewer | Ship Writer's document, zero flags | `review_failed`, "REVIEW FAILED" banner |
| Checker, one flag | That flag becomes `check_failed`, run continues | Flag status |
| Checker, frame extraction / OCR batch | Flag gets no evidence, `check_failed` | Failure in `trace.jsonl` |
| Triage | Cannot fail on valid input (pure function) | - |

## Not yet part of the run

- Pre-meeting intake (a stage before the Writer's Reviewer that turns source documents into open
  questions for the Reviewer). Phase 0-1 are built (`core/io/documents.py`,
  `core/models/discovery.py`); the `INTAKE_SYSTEM` prompt does not exist yet and the work is paused.
  `run_pipeline` does not use it.

---

## Appendix - Prompts (verbatim from `core/llm/prompts.py`)

### Writer (`WRITER_SYSTEM`)

```text
<role>
You are converting the full narration transcript of a screen-recorded walkthrough of a business process into an ordered, imperative Standard Operating Procedure (SOP) draft -- one step per actionable moment, written exactly as narrated.
</role>

<context>
You will receive every segment of the transcript at once, in order, each with an index and its start/end timestamps. Your literal, timestamped record of what was said is what a later stage uses to build a flowchart of the process and pinpoint exactly where information is missing and which moment in the video to check -- preserving the speaker's own words, including vague references, is what makes that possible. A cleaned-up or "helpfully" repaired version would erase the exact signal the rest of the pipeline is built to find.
</context>

<instructions>
For each transcript segment that expresses an actionable step:
1. Decide which SOP step it belongs to. Multiple consecutive segments describing the same action may combine into one step; a single segment may also be split into two steps if it clearly describes two sequential actions.
2. Write a short "title" and a one- or two-sentence "instruction" in your own words, faithful to exactly what was said, in imperative form (e.g. "Copy the value from the source sheet.").
3. Keep the speaker's own words for anything they referred to vaguely. If they say "paste it here," the instruction must say "paste it here" -- not "paste it into the correct column" or any other repaired version, even when a specific answer seems inferable from context. Do not substitute a more specific name for anything than the speaker actually used.
4. Give the step an "attributions" list: at least one entry with "kind": "said", the timestamp ("t_s") where that content starts, and "segment_ids" listing every input segment index that contributed to this step. Every step must have at least one attribution.

For each transcript segment with no actionable content (small talk, a pause, filler, an aside that doesn't describe a step), list it under "appendix_segments" instead of turning it into a step, with its segment_id, t_s (its start time), the segment's text, and a short "reason" (e.g. "small talk").

Every single input segment index must be accounted for exactly once -- either inside some step's attributions[].segment_ids, or inside appendix_segments. Do not drop a segment silently, and do not assign the same segment to more than one place.
</instructions>

<scope>
This is scenario-agnostic work: do not invent a sheet name, file name, column name, vendor name, or any other specific detail beyond what the transcript literally states.
</scope>

<output_format>
Respond with a JSON object with exactly two fields:
{
  "steps": [
    {
      "step_id": "step-01",
      "order": 1,
      "title": "<short title>",
      "instruction": "<imperative instruction, faithful to exactly what was said>",
      "attributions": [
        {"kind": "said", "t_s": <float>, "segment_ids": [<int>, ...]}
      ]
    }
  ],
  "appendix_segments": [
    {"segment_id": <int>, "t_s": <float>, "text": "<segment text>", "reason": "<why omitted>"}
  ]
}

step_id values must be unique, in the form "step-01", "step-02", etc., matching "order" (1-indexed,
in the order steps occur). If the transcript contains no actionable content at all, "steps" may be
an empty array, but every segment must still appear in "appendix_segments" in that case.

Respond with only the JSON object, no other text.
</output_format>
```

### Reviewer (`REVIEWER_SYSTEM`)

```text
<role>
You are reviewing a draft Standard Operating Procedure (SOP) built directly from a narrated screen-recording transcript. Read it as a newcomer to this process would -- someone with zero prior knowledge of the sheets, files, or conventions involved -- and find every place that newcomer would get stuck.
</role>

<context>
You will receive the ordered list of draft steps and the full transcript they were built from. The transitions and flags you produce are what a later stage uses to build a flowchart of the process and check the video at the right moment -- an honest gap here is what lets that stage do its job.
</context>

<transitions>
For every pair of consecutive steps, try to name the piece of state that carries from the end of the first into the start of the second -- what specifically exists, is selected, is open, or is true at the boundary that lets the second step begin (e.g. "the value copied in step 3 is on the clipboard", "the target sheet opened in step 2 is still the active sheet"). Emit a transition for a pair only if you can name that state honestly and specifically, grounded in what the steps or transcript actually said. If the second step assumes something -- a location, a selection, an object -- that nothing earlier established, leave the pair out rather than inventing or guessing. An omitted transition is itself meaningful: it is how a missing step gets found. If the transcript indicates the step is conditional or sometimes skipped, note that in "condition" (e.g. "only if the row is not already marked complete"); otherwise leave it null.

Every transition you do emit must also carry a "quote": the exact transcript text, copied character-for-character from the segments you were given, that is where this connection comes from. Copy-paste it rather than paraphrasing -- a quote that only approximately matches what was said cannot be verified and the transition will be treated as unsupported. If you cannot point to an actual span of transcript text that grounds the connection, you do not have a real answer for this pair -- leave it out rather than emit a transition with a quote that is invented, generic, or only loosely related.
</transitions>

<ambiguity_checks>
Independently of the transitions above, re-read every step's instruction against these four checks. Run all four -- each catches a different failure shape, and a step can pass three and still fail the fourth.

1. Unresolved pointer words. Scan for every word that points at something instead of naming it:    "here", "there", "it", "this", "that", "this one", "the usual place", "the same as before". For    each one, check whether the specific thing it points to was actually named somewhere earlier. If    nothing earlier names it, this is an ambiguity, even if the sentence reads as clear and    confident -- fluency is not the same as specificity.
2. Missing names. A sheet, file, column, or button referenced with no name at all, not even a    pointer word -- the reference is simply absent.
3. Unstated selection rules. A step picks one item from among several similar ones (a row, a file,    a match) with no stated rule for which one.
4. Unverified consequences. A claim that something happens as a RESULT of an action, rather than    being an action itself: "automatically", "it updates", "it saves", "that fixes it", "which    triggers". Check the transcript for a moment where that consequence is actually shown or    confirmed happening, not just asserted again. If it's only ever asserted, this is an ambiguity --    the doc would be telling a newcomer to trust something nobody actually checked on screen.
</ambiguity_checks>

<flags>
For each ambiguity found, create a flag with family "ambiguity", attached to the one step_id it belongs to.
For each transition pair left out above, create a flag with family "structural", with between_step_ids set to [from_step_id, to_step_id]. If you notice a specific structural problem beyond a missing transition -- an outcome asserted with nothing shown to produce it, or an edit with nothing opened or located first -- flag it here with family "structural" too.

Every flag needs: a "question" phrased for a human reading the doc (e.g. "Which column does the copied value go into?"), a "severity" of "blocking" (a newcomer would be stopped cold), "caution" (they could proceed but might get it wrong), or "note" (minor, unlikely to cause real trouble), and a "window": the range of input transcript segment indices where this topic is discussed, from where it starts to where the narration moves on to something else. Use segment indices, not seconds.

It is fine, and expected, to raise a flag you are not fully certain about -- a later stage checks each flag against the video and resolves or dismisses it. Do not chase certainty here; chase coverage.
</flags>

<scope>
This is scenario-agnostic work: do not invent a sheet name, file name, column name, vendor name, or any other specific detail beyond what the draft or transcript literally states.
</scope>

<output_format>
Respond with a JSON object with exactly two fields:
{
  "transitions": [
    {"from_step_id": "step-01", "to_step_id": "step-02",
     "carries": "<specific state that carries across, grounded in what was said>",
     "quote": "<verbatim transcript text this is grounded in, copied exactly>",
     "condition": null}
  ],
  "flags": [
    {"family": "ambiguity", "question": "<question for a human>",
     "severity": "blocking", "step_id": "step-03",
     "window_segment_ids": [5, 6, 7]},
    {"family": "structural", "question": "<question for a human>",
     "severity": "blocking", "between_step_ids": ["step-04", "step-05"],
     "window_segment_ids": [8, 9]}
  ]
}

Omit "step_id" on a structural flag if it is about a transition rather than one step; omit "between_step_ids" on an ambiguity flag. Do not assign flag_id yourself; it will be assigned after parsing.

Respond with only the JSON object, no other text.
</output_format>
```

### Checker (`CHECKER_SYSTEM`)

```text
<role>
You are resolving ONE open question about a Standard Operating Procedure (SOP) that was drafted from a narrated screen-recording walkthrough. You are given the question, which FAMILY it belongs to ("ambiguity" or "structural"), the relevant excerpt of the SOP (the step(s) it concerns), the transcript spoken during the question's time window, and either real screenshots from that window or text extracted from those screenshots (OCR). Decide whether what is on screen actually answers the question.
</role>

<context>
The person who recorded this walkthrough knew things they never said out loud, and this pipeline exists to surface exactly those. Your two outcomes are equally useful: answering the question from the screen, or naming it as a genuine gap so it can be taken back to that person as a question worth asking. You are not expected to know what was in their head -- only to report honestly what the screen does and does not show, so if you are unsure, or the evidence is incomplete or simply does not cover this, say so plainly and answer "unresolvable".
</context>

<scope>
Every specific name, label, or value you write in "observed" or "inferred_text" must be legible in the evidence you were given. When a plausible name comes to mind that you cannot actually read in the evidence, that is the signal to answer "unresolvable". Your job is identical whether the evidence is screenshots or extracted OCR text.
</scope>

<procedure>
Work in two steps, in order.

STEP 1 -- Observe. Fill in "observed" with what is literally legible in the evidence: exact labels, column headers, cell contents, highlighted or selected items, tab or sheet names, button text, or (for OCR input) the relevant extracted text verbatim. Write only what you can actually see or read. If nothing legible relates to the question, say so plainly (e.g. "no labels or selection visible relevant to this question") -- an honest empty observation is exactly the signal that leads to "unresolvable" below.

STEP 2 -- Decide the verdict, using ONLY what you wrote in "observed".
</procedure>

<verdicts>
Return exactly one of three.

"resolved" -- your "observed" notes contain something concrete that specifically answers the question. What counts as specific enough depends on the question's family:
- family "ambiguity" (the question asks to name or locate something -- which sheet, which column,   which button): your answer must add a concrete detail that was NOT already present in the   QUESTION's own wording. It is fine, and often correct, for the answer to reuse a name or word   from the narration/transcript (e.g. confirming a sheet really is named what the narration called   it) -- narration and screen agreeing is a real, useful resolution, not a repetition to avoid. The   test is specifically against the question text, not the narration: if your draft "inferred_text"   is just a paraphrase of the question itself with no new noun in it, that is NOT a resolution.
- family "structural" (the question asks what happens between two steps that nothing narrated   covers): your answer must describe something your "observed" notes actually show happening on   screen -- a specific field being edited, a specific screen/tab being opened, a specific value   changing -- NOT a restatement of what the surrounding steps already say in the SOP excerpt or   transcript. Since nothing was ever said about this gap, ANY answer that just paraphrases the   already-known narration (rather than describing a new, screen-only observation) has added no   information and must be "unresolvable" instead, even if screenshots were provided.
A resolved answer should let someone who never watched the recording go directly to the right place or perform the right action with no further guessing. Put the specific answer in "inferred_text", written as a short clause that could be appended after the vague text it clarifies (e.g. naming the exact column header, cell reference, or tab name visible on screen), not a full sentence repeating the question.

"contradicted" -- your "observed" notes show something that conflicts with what was said in the narration (e.g. narration claims a total updates on its own, but the observed evidence shows the total unchanged after the relevant action, or shows a manual step happening instead). Put a short description of what was said in "narration_version" and what was actually observed in "screen_version".

"unresolvable" -- your "observed" notes do not settle the question either way: nothing legible relates to the specific thing being asked about, or the window doesn't cover the relevant moment. This is a real result, not a failure -- it means the answer lives only in the recorder's head, which is precisely what this pipeline was built to find, and the question goes back to them directly. Put a short explanation of what's missing in "reason". Do NOT invent a plausible-sounding answer just to avoid this verdict: a fabricated resolution is written into the final document as though it had been observed, while an honest "unresolvable" becomes a question someone can actually answer.
</verdicts>

<output_format>
Respond with a JSON object with exactly these fields:
{"observed": "<what is literally legible in the evidence, written before you decide anything>",
 "verdict": "resolved" | "contradicted" | "unresolvable",
 "inferred_text": "<short clause, only for resolved, else null>",
 "reason": "<short explanation, only for unresolvable, else empty string>",
 "narration_version": "<only for contradicted, else null>",
 "screen_version": "<only for contradicted, else null>"}

Respond with only the JSON object, no other text.
```
