"""Single source of truth for the pipeline explorer and the decision register.

`docs/25-decision-register.md` and `docs/pipeline-explorer.html` are both rendered from this module by
scripts/build_pipeline_explorer.py, so they cannot disagree with each other. Two kinds of content:

- Written here: what each stage does, its decision tree (steps, conditions, what happens on each
  branch), failure behaviour, which decisions apply, measured results, open questions.
- Pulled live from the code, so it cannot drift: every prompt (core/llm/prompts.py), every threshold value
  (core/config.py), and the decision tables (docs/22-architecture-v3.md D1-D20, docs/adr/*.md).

When the pipeline changes, edit the stage here, run `python -m scripts.build_pipeline_explorer`, and commit
the regenerated files. tests/test_pipeline_explorer.py fails if they are stale.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from core import config
from core.llm import prompts

DOCS = Path(__file__).resolve().parents[2] / "docs"

# Tone of a branch: ok (normal path), warn (degrades, run continues), stop (run or item ends).
OK, WARN, STOP = "ok", "warn", "stop"


def _b(cond: str, then: str, tone: str = OK) -> Dict[str, str]:
    return {"if": cond, "then": then, "tone": tone}


def _s(step: str, note: str = "", branches: List[Dict[str, str]] | None = None) -> Dict[str, Any]:
    return {"step": step, "note": note, "branches": branches or []}


# --------------------------------------------------------------------------------------------------
# Stages, in the order a run reaches them. `lane` places a box in the flow diagram.
# --------------------------------------------------------------------------------------------------
STAGES: List[Dict[str, Any]] = [
    {
        "id": "input",
        "num": "In",
        "name": "Input and guards",
        "lane": "input",
        "kind": "code",
        "status": "built",
        "short": "A recording comes in. Anything unusable is refused before money is spent.",
        "inputs": ["A narrated screen recording (video with audio)", "Optional pre-meeting context files (planned, not built)"],
        "outputs": ["Run directory with a copy of the video", "manifest.json (code version, prompt hashes, settings, input hash)"],
        "model": "none",
        "prompts": [],
        "tree": [
            _s("Validate the file (core/guards.py)", branches=[
                _b("Not a file, or empty", "Refuse with the reason", STOP),
                _b("Larger than MAX_VIDEO_BYTES", "Refuse and name the limit", STOP),
                _b("Duration unreadable (corrupt, or ffprobe missing)", "Refuse", STOP),
                _b("Longer than MAX_VIDEO_DURATION_S", "Refuse: only short recordings are validated", STOP),
                _b("Otherwise", "Accept and continue"),
            ]),
            _s("Copy the video into runs/<run_id>/ and write manifest.json", "Code version, hash of every prompt, models, thresholds, input hash."),
            _s("Before every model call, check the cache (core/llm/cache.py)", branches=[
                _b("Same provider, model, prompts, input, schema, images and temperature seen before", "Return the stored answer. Cost: $0."),
                _b("Anything differs", "Call the provider and store the answer"),
            ]),
        ],
        "failure": "A refused input stops the run before any file is copied or any call is made.",
        "knobs": ["MAX_VIDEO_BYTES", "MAX_VIDEO_DURATION_S", "MAX_RUN_COST_USD", "KEEP_RUN_MEDIA", "CACHE_ENABLED", "LLM_TEMPERATURE", "LLM_TIMEOUT_S"],
        "measured": ["A second identical run cost $0.00 and produced the same sop.json (verified on the real clip)."],
        "issues": ["Recordings may contain client data: see docs/data-handling.md. No redaction, no encryption at rest."],
    },
    {
        "id": "transcribe",
        "num": "0",
        "name": "Transcription",
        "lane": "speech",
        "kind": "code",
        "status": "built",
        "short": "Audio to timed words. No reasoning, only timing and text.",
        "inputs": ["Audio extracted from the video (ffmpeg)"],
        "outputs": ["Segments: id, start, end, text", "Word-level timestamps attached to each segment (OpenAI path)"],
        "model": "Whisper (OpenAI) or Saaras v3 (Sarvam)",
        "prompts": [],
        "tree": [
            _s("Extract audio, then transcribe with the configured provider", branches=[
                _b("OpenAI", "Whisper with word and segment timestamps; words are attached to the segment that contains them"),
                _b("Sarvam", "Batch speech-to-text job; chunk timings parsed into segments; no word timing"),
            ]),
            _s("Check the timing", branches=[
                _b("One whole-transcript segment with no timing", "Mark timing_degraded: the document says its timestamps are unreliable, and the screen path is skipped", WARN),
                _b("Normal per-segment timing", "Continue"),
            ]),
        ],
        "failure": "Fatal. There is no document yet, so a failed transcription ends the run (the cost is recorded).",
        "knobs": ["MODEL_WHISPER", "SARVAM_MODEL_STT"],
        "measured": ["Narration can lag or lead the action by 4 to 5 seconds (docs/24), which is why word times alone cannot align speech to screen."],
        "issues": ["The Sarvam path has never been run against the real API in this project."],
    },
    {
        "id": "writer",
        "num": "1",
        "name": "Writer",
        "lane": "speech",
        "kind": "llm",
        "status": "built",
        "short": "Turns the transcript into ordered steps, deliberately naive: a vague 'paste it here' stays vague.",
        "inputs": ["Every transcript segment at once (index, start, end, text)"],
        "outputs": ["SOPDocument with steps (title, instruction, 'said' attributions) and appendix segments (small talk, with a reason)"],
        "model": "MODEL_TEXT (gpt-4o) or sarvam-105b, one call",
        "prompts": ["WRITER_SYSTEM"],
        "tree": [
            _s("One model call over the whole transcript", "Whole context at once, so a step can span several segments."),
            _s("Check the answer in code", branches=[
                _b("A segment is missing, or counted twice", "Reject", STOP),
                _b("A step has no 'said' attribution", "Reject: provenance is mandatory", STOP),
                _b("Every segment accounted for exactly once, every step attributed", "Build the SOPDocument"),
            ]),
            _s("Keep the speaker's own words", "The prompt forbids substituting a more specific name than the speaker used, and a test asserts that specifics never narrated do not appear."),
        ],
        "failure": "Fatal and the only fatal stage after transcription. No fallback: with no document there is nothing for later stages to work on.",
        "knobs": ["MODEL_TEXT", "LLM_TEMPERATURE"],
        "measured": ["Step count varied between runs on the same clip (5, 8, 9), which changes everything downstream."],
        "issues": [],
    },
    {
        "id": "reviewer",
        "num": "2",
        "name": "Reviewer",
        "lane": "speech",
        "kind": "hybrid",
        "status": "partial",
        "short": "Finds where a newcomer would get stuck, by omission: a step pair with no verified connection becomes a gap.",
        "inputs": ["Draft steps", "The full transcript"],
        "outputs": ["Transitions (what carries between steps, with a verbatim quote)", "Flags: ambiguity or structural, each with a question, severity and window"],
        "model": "MODEL_TEXT or sarvam-105b, one call, then plain Python",
        "prompts": ["REVIEWER_SYSTEM"],
        "tree": [
            _s("Model: for each consecutive pair, name what carries across, with a quote copied from the transcript", "Only when it honestly can. There is no ok/broken field to self-report."),
            _s("Model: run four ambiguity checks on every step", "Unresolved pointer words; missing names; unstated selection rules; unverified consequences ('it updates')."),
            _s("Code: coverage check (merge_reviewer_output)", branches=[
                _b("Transition has a quote that occurs in the transcript", "The pair is covered"),
                _b("Quote invented, paraphrased, empty, or the pair omitted", "Treated as silence: synthesize a structural flag", WARN),
                _b("The model already raised its own structural flag for that pair", "Keep the model's specific flag; skip the generic one"),
                _b("Transition between steps that are not consecutive", "Dropped"),
            ]),
        ],
        "failure": "Non-fatal. On any failure the Writer's document ships unchanged with review_failed=True and zero flags, and sop.md shows a REVIEW FAILED banner.",
        "knobs": ["MODEL_TEXT", "LLM_TEMPERATURE"],
        "measured": [
            "10 runs on one fixed draft: grounded transitions 0 to 2 (zero in 4 of 10); structural flags 3 to 6; ambiguity flags 2 to 5; total flags only 7 to 9.",
            "Quote-grounding stopped invented quotes counting as coverage; it did not stop the model skipping the task.",
        ],
        "issues": ["Flag count is unstable between identical runs. What 'stable' should mean has not been decided (see Open questions)."],
    },
    {
        "id": "screen",
        "num": "0b",
        "name": "Screen timeline",
        "lane": "screen",
        "kind": "code",
        "status": "built",
        "short": "Pixels only, no model: turns the video into discrete change events with before and after frames.",
        "inputs": ["The video"],
        "outputs": ["screen_events.json: events, scenes, per-sample activity", "Full-resolution before/after keyframes per event"],
        "model": "none (local OpenCV; local RapidOCR for scene labels)",
        "prompts": [],
        "tree": [
            _s("Sample frames at SCREEN_FPS, downscale, grayscale, blur"),
            _s("Difference against the previous sample, find changed boxes (diff_boxes)", branches=[
                _b("Changed fraction below SCREEN_ACTIVE_FRAC", "Treat as noise (cursor, compression); not active"),
                _b("At or above it", "Active sample"),
            ]),
            _s("Group active samples into events", "A run ends after SCREEN_SETTLE_S of stillness; a burst after a still gap starts a new event."),
            _s("Keyframes: last still sample before the change, last active sample after it", "Both stored at full resolution."),
            _s("Classify the event from the net before/after difference", branches=[
                _b("After frame is the before frame shifted vertically", "large_shift (a scroll)"),
                _b("Changed boxes span at least SCREEN_SCENE_COVERAGE of the frame AND at least SCREEN_SCENE_MIN_CHANGED of pixels changed", "scene_cut (a context switch); OCR the top and bottom strips as its label"),
                _b("Otherwise", "local (a cell, field or selection changed)"),
            ]),
        ],
        "failure": "Non-fatal. On failure the run records screen_failed and continues as v2: no silent-step flags, no event evidence.",
        "knobs": ["SCREEN_FPS", "SCREEN_ACTIVE_FRAC", "SCREEN_PIXEL_THRESH", "SCREEN_MIN_BOX_AREA", "SCREEN_SETTLE_S", "SCREEN_SCENE_COVERAGE", "SCREEN_SCENE_MIN_CHANGED", "SCREEN_SCROLL_MIN_SHIFT_PX", "SCREEN_SCROLL_MIN_RESPONSE"],
        "measured": [
            "Found 7 events on the real 24 s clip, matching the draft labels (the labels were read off the same frames, so this proves little about recall).",
            "A spreadsheet sheet switch changed only about 10.8% of pixels, so a 'more than 40% changed' scene-cut rule never fires.",
        ],
        "issues": ["Thresholds were tuned on one clip. Two actions inside one second merge into one event. Web and ERP screens untested."],
    },
    {
        "id": "align",
        "num": "2b",
        "name": "Align",
        "lane": "screen",
        "kind": "code",
        "status": "partial",
        "short": "Attaches screen events to flags. Also the fallback silent-step detector if the Reconciler fails.",
        "inputs": ["Steps and flags", "Transcript timing", "Screen events"],
        "outputs": ["Each flag carries the ids of the events near it", "Silent-step flags (only in fallback mode)"],
        "model": "none",
        "prompts": [],
        "tree": [
            _s("Attach events to every existing flag", "An event counts if it falls inside the flag's window or up to SCREEN_ALIGN_LEAD_S before it: action leads speech, never trails it."),
            _s("Silent-step detection (only when the Reconciler is off or failed)", branches=[
                _b("Event inside a step's spoken span, or shortly before it", "Owned by that step"),
                _b("No step owns it", "Candidate silent step", WARN),
                _b("A step owns more than SCREEN_ALIGN_MAX_EVENTS_PER_STEP events", "The extras are candidates", WARN),
                _b("A flag already covers the same step pair", "No duplicate flag"),
            ]),
        ],
        "failure": "Non-fatal: records align_failed and returns the document unchanged.",
        "knobs": ["SCREEN_ALIGN_LEAD_S", "SCREEN_ALIGN_MAX_EVENTS_PER_STEP"],
        "measured": ["On the real clip the Reviewer's flags already covered every event, so it raised no silent flag. Unproven on any data."],
        "issues": ["A fixed time window cannot align speech to screen: real segments are about 6 s long and narration lags the action. Superseded by the Reconciler for silent-step detection."],
    },
    {
        "id": "reconcile",
        "num": "2c",
        "name": "Reconciler",
        "lane": "merge",
        "kind": "hybrid",
        "status": "partial",
        "short": "For every step, says what changed on screen, quoting the OCR records. Anything it cannot quote is thrown away.",
        "inputs": ["Steps with word-timed speech", "One OCR change record per screen event (chrome before/after, changed regions before/after)"],
        "outputs": ["Observed changes on each step ('Observed on screen: ... [observed @t]')", "Flags: unspecified questions, said-but-not-shown steps, unsupported claims, silent events"],
        "model": "MODEL_TEXT or sarvam-105b, one call, then plain Python",
        "prompts": ["RECONCILER_SYSTEM"],
        "tree": [
            _s("Code: build change records by local OCR (cached by image content)", "Top and bottom strips before and after, plus the largest changed regions before and after."),
            _s("Model: one batched call over all steps and all records", "Per step: events, what changed, evidence quotes, unspecified questions. Per event: its step or none. Per claim: supported or not."),
            _s("Code: verify every quote", branches=[
                _b("Quote (spaces and pipes removed) is in the record of an event the model declared for that step", "Keep the 'what changed' sentence"),
                _b("Not found, or taken from another event", "Drop the sentence; the step counts as said-but-not-shown", WARN),
            ]),
            _s("Code: create flags (origin = reconcile)", branches=[
                _b("Step has no grounded change", "Flag: narrated but nothing on screen matches; created already unresolvable", WARN),
                _b("Model listed an unspecified question", "Flag it (capped per step, duplicates dropped); already unresolvable: the evidence was read, so it goes to the recorder"),
                _b("A claimed consequence is not supported by a grounded quote", "Flag: blocking if the records show no change, caution if unclear", WARN),
                _b("An event no step explains", "Open silent-step flag; the Checker will describe it"),
            ]),
        ],
        "failure": "Non-fatal. On any failure, or if no OCR engine is installed, it records reconcile_failed and falls back to Align's counting heuristic.",
        "knobs": ["RECONCILER_MAX_QUESTIONS_PER_STEP", "SCREEN_RECORD_MAX_REGIONS", "SCREEN_RECORD_MAX_CHARS", "SCREEN_RECORD_CROP_PAD_PX"],
        "measured": [
            "First run: 2 of 6 required findings; every 'observed' line was a real fragment of a record, nothing invented.",
            "Described window chrome and missed the cell references that carry the useful detail (a prompt change to name locators is unmeasured: API credit ran out).",
            "Mapped 'paste' to the sheet-switch event, not the event where the value appeared.",
            "Marked 'the total updates' supported from the status bar's sum of the selected cell. Grounding checks a quote exists, not what it means.",
        ],
        "issues": ["False support for consequence claims has no fix yet.", "Record extraction is spreadsheet-shaped; web and ERP surfaces need their own profile."],
    },
    {
        "id": "checker",
        "num": "3",
        "name": "Checker",
        "lane": "merge",
        "kind": "llm",
        "status": "partial",
        "short": "Resolves each open flag against the screen. Writes down what it saw before deciding, and 'unresolvable' is a valid answer.",
        "inputs": ["Open flags, most severe first", "Screen events attached to each flag (v3) or video frames (v2 path)"],
        "outputs": ["A verdict per flag: resolved, contradicted or unresolvable", "Inferred clauses on steps, or new inferred steps"],
        "model": "MODEL_VISION (gpt-4o-mini) for images, MODEL_TEXT for text-only; or sarvam-105b over OCR text",
        "prompts": ["CHECKER_EVENT_SYSTEM", "CHECKER_SYSTEM"],
        "tree": [
            _s("Select flags", branches=[
                _b("Status is open, sorted blocking first; first MAX_CHECKED_FLAGS", "Check them"),
                _b("Beyond MAX_CHECKED_FLAGS", "Marked unchecked, never dropped", WARN),
                _b("Estimated spend reaches MAX_RUN_COST_USD", "Stop starting new flags; the rest are marked unchecked", WARN),
            ]),
            _s("Per flag: choose the evidence path", branches=[
                _b("Flag has attached screen events (v3)", "Event path (below)"),
                _b("No events", "v2 path: extract up to CHECKER_MAX_FRAMES_PER_FLAG frames from the flag's window; OpenAI sends them to a vision model, Sarvam OCRs them in one batch then reasons over text"),
            ]),
            _s("Event path, level 0: nearest event, OCR text, text model", "Evidence: full downscaled frame, numbered before/after tiles, OCR of chrome and regions."),
            _s("Read the verdict", branches=[
                _b("resolved or contradicted", "Apply it"),
                _b("resolved, but the observation shares CHECKER_ECHO_MAX or more of its words with the prompt", "Downgrade to unresolvable (it restated the prompt)", WARN),
                _b("unresolvable", "Level 1: the events around it (plus or minus SCREEN_ESCALATION_EVENTS) and, on OpenAI, the images; then stop. Maximum two calls per flag"),
            ]),
            _s("Apply a resolution", branches=[
                _b("Ambiguity flag resolved", "Append an inferred clause to its step; the narrated text is never rewritten"),
                _b("Structural flag resolved", "Insert a new inferred step between the two steps"),
                _b("contradicted", "Flag becomes blocking"),
                _b("unresolvable", "Terminal status; it goes back to the recorder as a question"),
                _b("The call fails", "That flag becomes check_failed; the run continues", WARN),
            ]),
        ],
        "failure": "Non-fatal per flag. One failed call marks that flag check_failed and the run continues.",
        "knobs": ["MAX_CHECKED_FLAGS", "CHECKER_MAX_FRAMES_PER_FLAG", "CHECKER_FRAME_INTERVAL_S", "SCREEN_EVIDENCE_FULL_WIDTH", "SCREEN_EVIDENCE_MAX_IMAGES", "SCREEN_ESCALATION_EVENTS", "CHECKER_ECHO_MAX", "MAX_RUN_COST_USD", "MODEL_VISION"],
        "measured": [
            "v2 on 5 real runs: 68% of sampled frame-to-frame differences were tiny; 15 of 38 flags failed on rate limits; about $0.04 to $0.06 per flag.",
            "v2 'observed' text often restated the prompt: mean echo score 0.34 (resolved), 0.54 (unresolvable).",
            "v3 on one run: $0.137 for the whole run, 0 failed flags, about $0.015 per flag.",
        ],
        "issues": [
            "Resolved is too easy: several resolved answers were generic and named nothing the question asked for.",
            "Structural flags can resolve into a confident false inserted step (seen in a real run).",
        ],
    },
    {
        "id": "triage",
        "num": "4",
        "name": "Triage",
        "lane": "merge",
        "kind": "code",
        "status": "built",
        "short": "A pure function of status and severity. No model. Decides what blocks, what warns, what proceeds.",
        "inputs": ["Every flag's status and severity"],
        "outputs": ["build_spec.json: steps, flags with triage verdicts, severity-sorted escalation queue"],
        "model": "none",
        "prompts": [],
        "tree": [
            _s("triage_flag(status, severity)", branches=[
                _b("status = resolved", "proceed_with_assumption"),
                _b("status = contradicted", "block_and_escalate"),
                _b("any other status, severity = blocking", "block_and_escalate", WARN),
                _b("any other status, severity = caution or note", "proceed_with_flag"),
            ]),
            _s("Build the escalation queue from every block_and_escalate flag, sorted by severity"),
        ],
        "failure": "Cannot fail on a valid document: it is a pure function.",
        "knobs": [],
        "measured": ["Same document in, same output out; tested exhaustively."],
        "issues": ["Only as consistent as the severity the Reviewer or Reconciler assigned."],
    },
    {
        "id": "outputs",
        "num": "Out",
        "name": "Outputs",
        "lane": "output",
        "kind": "code",
        "status": "built",
        "short": "One validated document, rendered deterministically for people and for tools.",
        "inputs": ["The final SOPDocument"],
        "outputs": [
            "sop.md: workflow as spoken (with observed changes), what we assumed, what is missing, questions to ask",
            "sop.json and build_spec.json (schema_version 3.0; JSON Schemas in schemas/)",
            "trace.jsonl: every call, payload, failure, retry and stage timing",
            "run.json, manifest.json, alignment.json, change_records.json",
        ],
        "model": "none",
        "prompts": [],
        "tree": [
            _s("Render sop.md from the document, never from a model", "Byte-deterministic, so diffing two runs is meaningful."),
            _s("Every line carries its evidence kind", branches=[
                _b("From the narration", "[said @t]"),
                _b("Read off the screen for a narrated step", "[observed @t, event]"),
                _b("Added by the Checker", "[inferred from screen @t]"),
            ]),
            _s("Invalid provenance", branches=[_b("A step without attribution, a flag without a window, a resolution without evidence", "The document cannot be constructed (a raising validator, not a soft flag)", STOP)]),
        ],
        "failure": "A document that fails provenance validation cannot exist, so the failing stage's own failure rules apply.",
        "knobs": ["KEEP_RUN_MEDIA"],
        "measured": [],
        "issues": ["A banner saying 'Nothing is missing' appeared in a run that missed three of the six required findings."],
    },
]

# Cross-cutting behaviour that applies to every model call, shown as its own box under the flow.
CROSS: List[Dict[str, Any]] = [
    {
        "id": "reliability",
        "num": "All",
        "name": "Retries, cache and limits",
        "lane": "cross",
        "kind": "code",
        "status": "built",
        "short": "How every model call fails, retries, caches and is capped.",
        "inputs": ["Every provider call"],
        "outputs": ["A result, a cached result, or a clean failure that the owning stage handles"],
        "model": "none",
        "prompts": [],
        "tree": [
            _s("A provider call raises", branches=[
                _b("Out of credit (insufficient_quota and similar)", "Fail now: waiting cannot fix it", STOP),
                _b("Rate limit, timeout, connection error, 5xx", "Retry with jittered exponential backoff up to RETRY_MAX_ATTEMPTS; each retry is written to the trace", WARN),
                _b("The answer does not match its schema", "Re-ask once (RETRY_MAX_PARSE_ATTEMPTS), then fail to the stage's own fallback", WARN),
                _b("Anything else (auth, bad request, a bug)", "Fail immediately", STOP),
            ]),
            _s("Spend", branches=[
                _b("Estimated cost reaches MAX_RUN_COST_USD", "The Checker stops starting flags; Writer and Reviewer are not interrupted", WARN),
                _b("Cache hit", "No call, no cost"),
            ]),
            _s("Every stage is bracketed by stage_start and stage_end trace events with a run id and duration"),
        ],
        "failure": "Each stage decides what a failure means (see the stage boxes). Only the Writer and transcription are fatal.",
        "knobs": ["RETRY_MAX_ATTEMPTS", "RETRY_MAX_PARSE_ATTEMPTS", "RETRY_INITIAL_WAIT_S", "RETRY_MAX_WAIT_S", "LLM_TIMEOUT_S", "LLM_TEMPERATURE", "MAX_RUN_COST_USD", "CACHE_ENABLED"],
        "measured": ["An out-of-credit error was retried five times before this rule existed; the first real out-of-credit failure exposed it."],
        "issues": ["The cache stores transcripts and model answers unencrypted on disk."],
    },
]

# Decisions that are not in docs/22's table or the ADRs but were settled and shape the design.
EXTRA_DECISIONS: List[Dict[str, Any]] = [
    {"id": "X1", "title": "Every step raises a 'what changed here?' question, answered from screen diffs", "why": "Gaps found only where the Reviewer is unsure missed the important ones: scored against the six required findings, existing runs surfaced 0 to 3. The diffs already carry cell references, formula text, tab names and before/after values.", "rejected": "Flag only uncertain steps (v2).", "status": "partial", "stages": ["reconcile"], "source": "docs/24"},
    {"id": "X2", "title": "One batched model call per stage over the whole context, not many small per-item calls", "why": "Whole context lets a step span several segments and lets the model see what carries between steps; per-item calls cannot.", "rejected": "A call per segment, per step, or per flag for the Writer, Reviewer and Reconciler (the Checker is per flag on purpose: each flag has its own evidence).", "status": "built", "stages": ["writer", "reviewer", "reconcile"], "source": "project principle"},
    {"id": "X3", "title": "Scenario facts live only in evals and tests, never in prompts or pipeline code", "why": "A prompt that names the answer is not testing whether the model can find it. Enforced by a grep guard over every prompt.", "rejected": "Worked examples drawn from the real test recording inside prompts.", "status": "built", "stages": ["writer", "reviewer", "reconcile", "checker"], "source": "tests/test_prompts_scenario_agnostic.py"},
    {"id": "X4", "title": "Quote verification tolerates OCR noise: whitespace and pipes are removed before matching", "why": "OCR drops and adds spaces, and records join fragments with pipes. Exact matching would reject true statements.", "rejected": "Exact substring match; a fuzzy similarity threshold (a fabricated value could pass it).", "status": "built", "stages": ["reconcile"], "source": "core/text_grounding.py"},
    {"id": "X5", "title": "Reconciler flags are created already unresolvable and skip the Checker", "why": "The Reconciler has already read the evidence; the Checker would see the same records and add cost without new information. These go to the recorder as questions.", "rejected": "Create them open and let the Checker re-read the same evidence.", "status": "built", "stages": ["reconcile", "checker"], "source": "core/screen/reconcile.py"},
    {"id": "X6", "title": "The Reconciler replaces Align's counting heuristic for silent steps; Align stays as the fallback", "why": "Counting events per step cannot tell a described action from an undescribed one in dense narration.", "rejected": "Keep the event-count rule as the only silent-step detector.", "status": "built", "stages": ["reconcile", "align"], "source": "docs/24"},
    {"id": "X7", "title": "Findings are scored against the six required findings with pattern groups, forbidden claims and a false-completeness check", "why": "Mechanics tests prove the code runs, not that the output is right. Without a target the pipeline could look healthy while missing the point.", "rejected": "Reading outputs by eye; a model judging its own pipeline's output.", "status": "partial", "stages": ["outputs"], "source": "evals/score_findings.py"},
]

DECISION_STAGES: Dict[str, List[str]] = {
    "D1": ["screen"], "D2": ["screen", "align"], "D3": ["input"], "D4": ["screen"], "D5": ["screen"], "D6": ["screen"],
    "D7": ["checker"], "D8": ["checker"], "D9": ["align"], "D10": ["writer"], "D11": ["reviewer"], "D12": ["align", "reconcile"],
    "D13": ["screen", "align"], "D14": ["reconcile"], "D15": ["screen"], "D16": ["input"], "D17": ["screen"], "D18": ["checker"],
    "D19": ["screen"], "D20": ["checker"],
    "ADR-0001": ["writer"], "ADR-0002": ["reviewer"], "ADR-0003": ["checker"], "ADR-0004": ["triage"],
    "ADR-0005": ["writer", "checker", "outputs"], "ADR-0006": ["screen"], "ADR-0007": ["checker"],
    "ADR-0008": ["reliability"], "ADR-0009": ["reviewer", "input"],
}

DECISION_STATUS: Dict[str, str] = {
    "D1": "built", "D2": "built", "D3": "built", "D4": "built", "D5": "built", "D6": "built", "D7": "built", "D8": "partial",
    "D9": "built", "D10": "built", "D11": "built", "D12": "built", "D13": "built", "D14": "planned", "D15": "planned",
    "D16": "partial", "D17": "built", "D18": "built", "D19": "partial", "D20": "built",
}

OPEN_QUESTIONS: List[Dict[str, Any]] = [
    {"id": "Q1", "title": "What should 'stable' mean for the Reviewer's flag counts?", "stages": ["reviewer"], "why": "Ten runs on one fixed draft gave 7 to 9 total flags but the structural/ambiguity split moved and grounded transitions ranged 0 to 2. The evals cannot score stability until the target is defined.", "options": ["A stable total", "The same flagged locations each run", "The same bucket for each issue"]},
    {"id": "Q2", "title": "How do we stop a consequence claim being marked supported by a change that is not the one claimed?", "stages": ["reconcile", "reviewer"], "why": "The Reconciler accepted the status bar's sum of the selected cell as proof that a sheet total updated. Quote checking confirms a quote exists, not what it means.", "options": ["Require the supporting quote to come from a region other than where the action happened", "Treat any claim about a total as open unless a record shows the value it would produce", "Ask the recorder instead of the model"]},
    {"id": "Q3", "title": "How do we stop the Checker resolving structural flags into confident false steps?", "stages": ["checker"], "why": "A real run inserted 'the pasted value updates the total in the Transactions-March sheet', which no event shows. It is the largest remaining source of false statements.", "options": ["Require an observation naming a changed value, not a description of the screen", "Do not insert steps from a single event", "Stricter specificity rule for 'resolved'"]},
    {"id": "Q4", "title": "Should events split when two actions fall inside one second?", "stages": ["screen", "reconcile"], "why": "Switching sheet and selecting a cell merged into one event, so 'copy' was mapped to nothing.", "options": ["Split on a changed-region jump", "Shorten the settle window", "Leave merged and let the Reconciler read both"]},
    {"id": "Q5", "title": "How are web and ERP surfaces handled?", "stages": ["screen", "reconcile", "checker"], "why": "Record extraction reads spreadsheet chrome (cell reference, formula bar, tabs). A web page has a URL, breadcrumb and field labels, and context switches are not 11% pixel changes there. Nothing has been tested.", "options": ["A per-surface chrome profile", "Detect chrome regions from what never changes between events", "Record a web walkthrough as a second eval case first"]},
    {"id": "Q6", "title": "Who confirms the event labels?", "stages": ["screen"], "why": "The draft labels were read off the detector's own keyframes, so a perfect score proves nothing about recall. A person has to watch the video.", "options": ["The recorder", "A reviewer on the engagement"]},
    {"id": "Q7", "title": "When does intake (outside context as questions, never answers) resume?", "stages": ["reviewer", "input"], "why": "It is the integration point for a discovery tool. Source ingestion is built; the intake prompt is not. Paused until the screen path is trusted.", "options": ["After the findings score is acceptable", "In parallel"]},
    {"id": "Q8", "title": "Will the workbook or other work file be available with the recording?", "stages": ["reconcile"], "why": "Why row 18 was chosen, and what the total formula depends on, are not on screen. Only the file recovers them (D14).", "options": ["Usually", "Sometimes", "Never"]},
    {"id": "Q9", "title": "How is client data protected at rest?", "stages": ["input", "reliability"], "why": "Runs and the cache are unencrypted; nothing is redacted before it is sent to the provider.", "options": ["Encrypted volume and scheduled cleanup (documented)", "Redaction before sending", "Local models only"]},
]


# --------------------------------------------------------------------------------------------------
# Loaders: live values and the decision tables
# --------------------------------------------------------------------------------------------------
def prompt_text(name: str) -> str:
    return getattr(prompts, name)


def knob_value(name: str) -> Any:
    value = getattr(config, name)
    return list(value) if isinstance(value, tuple) else value


_D_ROW = re.compile(r"^\| (D\d+) \| (.+?) \| (.+?) \| (.+?) \|\s*$")


def parse_d_decisions() -> List[Dict[str, Any]]:
    out = []
    for line in (DOCS / "22-architecture-v3.md").read_text(encoding="utf-8").splitlines():
        m = _D_ROW.match(line)
        if m:
            did, title, why, rejected = m.groups()
            out.append(
                {
                    "id": did,
                    "title": title.strip(),
                    "why": why.strip(),
                    "rejected": rejected.strip(),
                    "status": DECISION_STATUS.get(did, "built"),
                    "stages": DECISION_STAGES.get(did, []),
                    "source": "docs/22",
                }
            )
    return out


def parse_adrs() -> List[Dict[str, Any]]:
    out = []
    for path in sorted((DOCS / "adr").glob("0*.md")):
        text = path.read_text(encoding="utf-8")
        num = path.name.split("-")[0]
        title = re.search(r"^# ADR \d+: (.+)$", text, re.M).group(1)
        status_line = re.search(r"^\*\*Status:\*\* (.+)$", text, re.M).group(1)
        section = lambda name: re.search(rf"^## {name}\n\n(.*?)(?=\n## |\Z)", text, re.M | re.S).group(1).strip()
        low = status_line.lower()
        status = "planned" if "not built" in low else ("partial" if ("open" in low or "unproven" in low) else "built")
        out.append(
            {
                "id": f"ADR-{num}",
                "title": title,
                "why": section("Context") + " " + section("Decision"),
                "rejected": section("Rejected alternatives"),
                "status": status,
                "status_note": status_line,
                "stages": DECISION_STAGES.get(f"ADR-{num}", []),
                "source": f"docs/adr/{path.name}",
            }
        )
    return out


def all_decisions() -> List[Dict[str, Any]]:
    return parse_d_decisions() + parse_adrs() + EXTRA_DECISIONS


def all_stages() -> List[Dict[str, Any]]:
    return STAGES + CROSS
