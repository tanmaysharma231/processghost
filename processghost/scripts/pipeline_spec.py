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
        "outputs": ["Segments: id, start, end, text", "Word-level timestamps attached to each segment"],
        "model": "Whisper (OpenAI)",
        "prompts": [],
        "tree": [
            _s("Extract audio, then transcribe with Whisper", "Word and segment timestamps; words are attached to the segment that contains them."),
            _s("Check the timing", branches=[
                _b("One whole-transcript segment with no timing", "Mark timing_degraded: the document says its timestamps are unreliable, and the screen path is skipped", WARN),
                _b("Normal per-segment timing", "Continue"),
            ]),
        ],
        "failure": "Fatal. There is no document yet, so a failed transcription ends the run (the cost is recorded).",
        "knobs": ["MODEL_WHISPER"],
        "issues": [],
        "measured": ["Narration can lag or lead the action by 4 to 5 seconds (docs/24), which is why word times alone cannot align speech to screen."],
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
        "model": "MODEL_TEXT (gpt-4o), one call",
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
        "model": "MODEL_TEXT, one call, then plain Python",
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
        "outputs": ["screen_events.json: events, scenes, per-sample activity", "Full-resolution before/after keyframes per event", "A peak frame for an event that came and went (a dropdown opened and closed)"],
        "model": "none (local OpenCV; local RapidOCR for scene labels)",
        "prompts": [],
        "tree": [
            _s("Sample frames at SCREEN_FPS, downscale, grayscale, blur"),
            _s("Find the formula-bar band once (spreadsheets)", "The row holding the formula-bar glyph, found with local OCR on an early frame. No glyph found: no band, and nothing below changes."),
            _s("Difference against the previous sample, find changed boxes (diff_boxes)", branches=[
                _b("Changed fraction below SCREEN_ACTIVE_FRAC", "Treat as noise (cursor, compression); not active"),
                _b("Below it, but the band's text differs from the previous sample's", "Active: a few characters edited in one cell move too few pixels to pass the whole-frame threshold. A small pixel change in the band only decides whether it is read; the reading decides (a moving pointer or a blinking caret changes pixels, not text)"),
                _b("At or above it", "Active sample"),
            ]),
            _s("Group active samples into events", "A run ends after SCREEN_SETTLE_S of stillness; a burst after a still gap starts a new event."),
            _s("Optional split (SCREEN_SPLIT_ON_LOCUS_SHIFT, off by default)", "After a still sample, a change that does not touch the run's earlier changes starts a new event: two quick actions in different places.", branches=[
                _b("Flag off (default)", "Changes inside the settle window stay one event"),
                _b("Flag on", "Split at the jump in where the screen is changing", WARN),
            ]),
            _s("Keyframes: last still sample before the change, the SETTLED sample after it", "The settled sample is one settle period past the last changed sample, never past the sample before the next event starts, so the final keystrokes of an edit are on it. Both stored at full resolution."),
            _s("Classify the event from the net before/after difference", branches=[
                _b("After frame is the before frame shifted vertically", "large_shift (a scroll); changes left after undoing the shift are kept as residual boxes, so an edit during a scroll is not hidden"),
                _b("Changed boxes span at least SCREEN_SCENE_COVERAGE of the frame AND at least SCREEN_SCENE_MIN_CHANGED of pixels changed", "scene_cut (a context switch); OCR the top and bottom strips as its label"),
                _b("Otherwise", "local (a cell, field or selection changed)"),
                _b("Before and after are identical but the screen changed in between", "transient: kept with its most-changed frame; an interaction, not a committed change"),
            ]),
        ],
        "failure": "Non-fatal. On failure the run records screen_failed and continues as v2: no silent-step flags, no event evidence.",
        "knobs": ["SCREEN_FPS", "SCREEN_ACTIVE_FRAC", "SCREEN_PIXEL_THRESH", "SCREEN_MIN_BOX_AREA", "SCREEN_SETTLE_S", "SCREEN_SCENE_COVERAGE", "SCREEN_SCENE_MIN_CHANGED", "SCREEN_SCROLL_MIN_SHIFT_PX", "SCREEN_SCROLL_MIN_RESPONSE", "SCREEN_SPLIT_ON_LOCUS_SHIFT", "SCREEN_LOCUS_MARGIN_PX", "SCREEN_BAND_SEARCH_PCT", "SCREEN_BAND_PAD_PX", "SCREEN_BAND_PREFILTER_FRAC", "SCREEN_BAND_MAX_OCR", "SCREEN_BAND_SEARCH_SAMPLES"],
        "measured": [
            "Found 7 events on the real 24 s clip, matching the draft labels (the labels were read off the same frames, so this proves little about recall).",
            "A spreadsheet sheet switch changed only about 10.8% of pixels, so a 'more than 40% changed' scene-cut rule never fires.",
        ],
        "issues": ["Thresholds were tuned on one clip. Two actions inside one second merge into one event unless the locus split is turned on (unmeasured). Transient, residual-box and split behaviour is tested on synthetic clips only. Web and ERP screens untested."],
    },
    {
        "id": "surface",
        "num": "0c",
        "name": "Surface locators, event classes and anchors",
        "lane": "screen",
        "kind": "hybrid",
        "status": "partial",
        "short": "Reads which application, sheet or page each frame shows from its chrome text, classifies every screen event by what changed (never by pixel size), and keeps selections as anchors tied to speech and later values by time. A cheap vision model reads only fields OCR could not.",
        "inputs": ["Screen events and keyframes", "OCR of each frame's top and bottom strips", "Word-timed speech (for the cross-check only)"],
        "outputs": ["Change records with a locator before and after, what kind of change it was, and its event class", "contexts.json: each distinct context once, reused when the recording returns", "A speech clue per context switch", "anchors.json: selections tied to speech or later values", "The list of screen activity that became no step, each with its reason"],
        "model": "none, except a cheap vision model (MODEL_VISION) for the few selected-cell fields OCR cannot read, one batched call",
        "prompts": ["READ_SYSTEM"],
        "tree": [
            _s("Per frame: pick the best-scoring profile (spreadsheet, web), else the generic fallback", branches=[
                _b("A profile scores at least SURFACE_MIN_CONFIDENCE", "Extract its locator: application, document, sheet or page, selected cell, formula-bar content, and the strings it came from"),
                _b("None does", "Generic locator: the first title fragment only, low confidence", WARN),
            ]),
            _s("Compare the before and after locators of each event", branches=[
                _b("Different application, document, sheet or page", "app_switch, document_switch, sheet_switch or page_navigation"),
                _b("Same surface", "same_surface_edit"),
                _b("Either side is not recognised", "unknown: never a guess", WARN),
            ]),
            _s("Build the context map", "A new context when the locator changes (or an unrecognised scene cut); the existing one is reused when the same sheet or page returns. Observations only: no hypotheses are stored."),
            _s("Repair unread selected-cell fields (spreadsheet only)", branches=[
                _b("The cell reference validates", "Nothing to do"),
                _b("It does not", "Re-read with a CHANGED input (2x upscale, local contrast, taller strip); identical pixels would give the identical wrong answer"),
                _b("Still not valid", "Queue the top strip; one batched call to the cheap vision model transcribes all queued strips. Its answer must pass the same validator, is tagged read_by=vlm, and is capped per run", WARN),
                _b("Over the cap or the answer fails validation", "The field stays unread, never guessed", WARN),
            ]),
            _s("Classify each event by state change, never by pixel size", branches=[
                _b("The screen changed and came back", "transient: dropped, listed with its reason"),
                _b("A scroll with nothing left after undoing the shift", "view_only: dropped, listed with its reason"),
                _b("Application, document, sheet or page changed", "navigation"),
                _b("Text differs before vs after at the same place, or the formula bar changed on the SAME cell", "data_change"),
                _b("Only the selected cell moved", "selection: kept as an anchor"),
                _b("Anything else (unreadable, mixed)", "unclear: always kept", WARN),
            ]),
            _s("Anchors: join selections to speech and later values by time", branches=[
                _b("A pointer word or the cell's value is spoken near it AND a later data change puts the same value elsewhere", "Confirmed anchor with a role (source)"),
                _b("Only one of the two", "Candidate, shown as 'possibly <cell>', never as fact"),
                _b("Neither", "Listed as unused screen activity", WARN),
                _b("The value could belong to more than one change, or two anchors hold it", "The value signal is withheld: ambiguity yields candidates, never a guess", WARN),
            ]),
            _s("Speech clue (corroboration, never a gate)", branches=[
                _b("The speaker named the destination within the window (mostly after the event, because narration lags)", "found, with the lag"),
                _b("Nobody named it", "No clue: a possible silent navigation, not a verdict", WARN),
                _b("The event stayed on one surface", "Not computed"),
            ]),
        ],
        "failure": "Non-fatal. On failure it records surface_failed and the run continues without locators; the Reconciler builds its own records as before.",
        "knobs": ["SCREEN_TEXT_CHANGE_MIN_RATIO", "ANCHOR_VALUE_WINDOW_S", "OCR_RETRY_VARIANTS", "VLM_READ_MAX_CROPS", "SURFACE_MIN_CONFIDENCE", "SURFACE_SPEECH_WINDOW_BEFORE_S", "SURFACE_SPEECH_WINDOW_AFTER_S", "SURFACE_SPEECH_MIN_RATIO", "CONTEXT_MAX_OCR_CHARS", "SCREEN_RECORD_MAX_REGIONS", "SCREEN_REGION_MAX_CROP_FRAC"],
        "measured": [
            "Real clip, no model calls: 3 contexts found, the second reused when the recording returned to it; every scored field (change kind, sheet position, sheet name, selected cell) matched the draft labels.",
            "The draft labels are not independent of the detector's own readings, so this is a regression check, not proof.",
            "OCR often garbles or drops the formula-bar glyph; the cell reference was recovered anyway, formula content only when legible. The third sheet showed no readable cell reference.",
        ],
        "issues": ["Event classes, anchors and the read fallback are unit-tested on synthetic records only; the class labels (expected_classes.json) are drafts a person has not confirmed.", "Pointer words are English only.", "The web profile has only synthetic tests; no web recording exists.", "A web-hosted spreadsheet scores as web when its URL and browser name outweigh the spreadsheet signals.", "A hosted vision model for unrecognised surfaces is deferred (it would send screen images to a provider)."],
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
        "inputs": ["Steps with word-timed speech", "One OCR change record per screen event (chrome before/after, changed regions before/after, and when available the surface locator, change kind and speech clue)"],
        "outputs": ["Observed changes on each step ('Observed on screen: ... [observed @t]')", "Flags: unspecified questions, said-but-not-shown steps, unsupported claims, silent events"],
        "model": "MODEL_TEXT, one call, then plain Python",
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
        "inputs": ["Open flags, most severe first", "Screen events attached to each flag"],
        "outputs": ["A verdict per flag: resolved, contradicted or unresolvable", "Inferred clauses on steps, or new inferred steps"],
        "model": "MODEL_VISION (gpt-4o-mini) for images, MODEL_TEXT for text-only",
        "prompts": ["CHECKER_EVENT_SYSTEM"],
        "tree": [
            _s("Select flags", branches=[
                _b("Status is open, sorted blocking first; first MAX_CHECKED_FLAGS", "Check them"),
                _b("Beyond MAX_CHECKED_FLAGS", "Marked unchecked, never dropped", WARN),
                _b("Estimated spend reaches MAX_RUN_COST_USD", "Stop starting new flags; the rest are marked unchecked", WARN),
            ]),
            _s("Per flag: choose the evidence path", branches=[
                _b("Flag has attached screen events", "Event path (below)"),
                _b("No events, or no screen timeline", "Marked unchecked: there is no evidence to check it against, so no model call is made", WARN),
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
        "knobs": ["MAX_CHECKED_FLAGS", "SCREEN_EVIDENCE_FULL_WIDTH", "SCREEN_EVIDENCE_MAX_IMAGES", "SCREEN_ESCALATION_EVENTS", "CHECKER_ECHO_MAX", "MAX_RUN_COST_USD", "MODEL_VISION"],
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
        "id": "document",
        "num": "3c",
        "name": "Document rung (workbook)",
        "lane": "merge",
        "kind": "hybrid",
        "status": "partial",
        "short": "Checks the workbook is the one in the recording, then answers what code can (which cell was used, what reads it, how rows are matched) and lets a short model loop try the rest, read-only. A formula states a rule; the screen only shows values. Every cited cell is checked.",
        "inputs": ["Open and unresolvable flags, and blocking flags the video resolved (a cross-check)", "The workbook, as a copy", "The cell trail: which cell was selected, on which sheet, when (read from the name box)", "Full-frame OCR of each sheet, for the match check"],
        "outputs": ["workbook_profile.json: sheets, headers, columns (with uniqueness), formulas as written, layout quirks", "cell_trail.json and workbook_match.json (match, partial, mismatch or unknown, with the reasons)", "Per flag: what the document rung did (answered, contradicted, candidates, no answer, skipped) with the cells it rests on", "New flags for conflicts the data revealed, left for the person who recorded the process"],
        "model": "MODEL_TEXT, a short loop of structured calls per flag, plus ONE call for the 'other paths' questions; the profiler, the facts table and the two lookup tools are plain code",
        "prompts": ["DATA_CHECKER_SYSTEM", "WONDER_SYSTEM", "VERIFY_READ_SYSTEM"],
        "tree": [
            _s("Check the workbook against the recording", branches=[
                _b("Sheet count disagrees", "mismatch: the workbook is NOT used, the report says so, every routed question is recorded as skipped", STOP),
                _b("A cell the recording CHANGED differs from the workbook", "Only a note: the file may be saved from before or after the recording. Every reading of the cell, before and after, is compared, not the first"),
                _b("A cell the recording did NOT change differs", "One batched second look at the cropped formula-bar row: the cheap vision model chooses between the reading, the workbook's value and neither/empty, and a choice of the workbook counts only if local OCR of the crop also shows its text. Pixels back the workbook: noted. Pixels back the reading: mismatch. Unclear or not confirmed: it stays against the workbook", STOP),
                _b("Only some words visible in the recording are not in the workbook", "partial: used, with a warning that answers are not verified against the recording", WARN),
                _b("Everything comparable agrees", "match"),
                _b("Nothing from the recording to compare", "unknown: stated in the report, never treated as a pass", WARN),
            ]),
            _s("Withdraw wrong attributions in the video's claims", "A number in a Checker claim that equals the value of a formula cell must be the value of a cell that depends, directly or through other formulas, on a cell this recording changed. Otherwise the claim is withdrawn (its inferred clause or step removed) and the question goes back to the person. Totals are computed from the cells because the file stores no results. It rejects wrong attributions; it cannot confirm a claim."),
            _s("Answer from facts, no model", branches=[
                _b("Effect: what else reads the cell that was changed", "The formulas that read it, with each literal criterion checked against the real cells; a criterion no cell satisfies becomes a new blocking question"),
                _b("Key: how the right row is picked", "Columns on the source sheet that carry the same text value as a cell in that row and are unique there; several candidates become a confirmation question, one is an answer"),
                _b("No trail cell to start from", "Left to the model loop; nothing is guessed", WARN),
            ]),
            _s("Name the parameters and what would break next time (no model)", branches=[
                _b("A value from the changed row sits in a repeating column and is typed into a formula as a literal", "A parameter (named from the column header), with a note that the formula must be changed for another period"),
                _b("A sheet name carries that value", "A note beside it, keeping the real sheet name: next period there will be another sheet, or this one is replaced"),
                _b("A repeating column with no such tie, or a sheet name that merely shares a word", "Not reported", WARN),
                _b("A step that is a result (something that happens by itself)", "Drawn as a result node off the action that causes it, with the formulas that produce it"),
            ]),
            _s("Find 'one shown, many needed' (no model)", "The range a formula reads that holds the changed cell, or the run of cells waiting for the same entry, says how many items the process covers; the recording changed one. Raised as a question for the person (\"1 of the 8 cells in F18:F25 changed; is it repeated for the remaining 7?\"), never added as a step. Needs a workbook."),
            _s("Wonder about the other paths: facts in code, one model call, a guard in code", branches=[
                _b("Facts table (no model)", "For the cells the recording changed or selected: the column's values with counts, its dropdown options (literal lists), the other columns of the same rows grouped by the changed one, ranges per group (a simple row formula such as =F-E is computed from the file), and how many formulas read the cell. Numbered F1, F2; capped; no row ids or raw rows"),
                _b("One call over the steps and the facts", "Notices that a recorded step sets one value out of several, or that rows in the same state are handled differently, and asks what decides it. It may only ask: never state a rule, threshold or reason"),
                _b("A question cites no real fact, or has a number or quoted value that is not in the facts it cites", "Dropped and counted in the trace (wonder_guard)", WARN),
                _b("A question states a reason, or is not a question, or was already asked", "Dropped", WARN),
                _b("Kept", "A flag for the person who recorded the process, with the facts and cells it rests on; never answered by a model"),
            ]),
            _s("Profile the workbook (no model)", "Sheets, the header row (not assumed to be row 1), column kinds, samples and distinct values for low-cardinality columns, formulas collapsed per column plus one-off formula cells in full, merged ranges, empty columns, blank rows, header names shared across sheets. Facts only; it says whether the file has calculated values at all."),
            _s("Route flags", branches=[
                _b("Not answered by the video (open, unresolvable, unchecked, check failed)", "Send, rule and key questions first"),
                _b("Resolved by the video but blocking", "Send as a cross-check: the screen can show one value while the real rule differs"),
                _b("Over DOCUMENT cap (DATA_MAX_ROUTED_FLAGS)", "Recorded as skipped, never dropped", WARN),
                _b("No workbook supplied", "Stage does not run"),
            ]),
            _s("Per flag: a loop of structured calls, each a lookup or a verdict", branches=[
                _b("Lookup (read_rows or query_rows)", "Row-capped, cells truncated, formulas returned as written; a bad sheet, column or operator is an error the model can read and a wasted call"),
                _b("The same lookup again", "Refused and counted as wasted", WARN),
                _b("DATA_MAX_CALLS_PER_FLAG lookups used", "One more round that can only give a verdict"),
            ]),
            _s("Check the verdict in code", branches=[
                _b("answered or contradicted, citing cells a lookup returned", "Keep it; value and formula are copied from the lookup result, not from the model"),
                _b("A cited cell was never returned", "Dropped; with none left the verdict is downgraded to no answer", WARN),
                _b("candidates", "Kept as possible answers; the question to the person becomes a confirmation"),
            ]),
            _s("Merge into the document", branches=[
                _b("Answered", "The question is resolved (a slot is filled), citing the cells; the base text is never edited"),
                _b("Contradicted, even a question the video resolved", "Becomes contradicted and blocking; the person decides", WARN),
                _b("Candidates or no answer", "An open question becomes unresolvable: every automated source has been tried"),
                _b("The data revealed a new conflict", "A new flag (origin data) for the person; never answered by the same call"),
            ]),
        ],
        "failure": "Non-fatal. A failed stage records document_failed; a failed question is skipped with the reason on the flag. The source workbook's hash is checked again at the end.",
        "knobs": ["DATA_ROW_CAP", "DATA_MAX_ROUTED_FLAGS", "DATA_CELL_MAX_CHARS", "DATA_DISTINCT_MAX", "DATA_MAX_CALLS_PER_FLAG", "DATA_MAX_ROUTED_FLAGS", "DATA_PASS_BUDGET_USD", "DATA_MAX_WORKBOOK_BYTES", "WONDER_MAX_CELLS", "WONDER_MAX_FACTS", "WONDER_MAX_COMPANIONS", "WONDER_MAX_VALUES_SHOWN", "WONDER_MAX_QUESTIONS"],
        "measured": [
            "Profiler and tools run on the real workbook with no model: they return the formula that states the rule for the total, and they show that no row holds the value that formula needs.",
            "First real run (before the facts, guard and match check existed): 30 model calls for 18 questions, $0.54; the key question was never answered, one answer cited the wrong month's row, and the Checker mixed the billed total with the actualized total.",
            "Replaying the new logic on that saved run with no model: the guard withdraws the mixed-up claim (the number is the value of a different total), the effect question is answered from formulas with the unmet 'Paid' condition raised, and the key question becomes a confirmation between two columns with their distinct counts.",
            "The match check found that the workbook differs from the one in the recording (a 'Total billed' row is missing); it also warns on OCR-garbled words, so a warning needs a person to read it.",
            "Second real run (with the facts, guard and match check): 9 of 10 findings on the corrected list, no forbidden claim, $0.31. The row key became a confirmation between two columns; the paste effect and the unmet Paid condition came from formulas, not a model.",
            "Afterwards, replayed on the saved run but not re-run with a model: a step with no located target uses the one cell the recording changed; only slot questions go to the model loop by default; repeat detection.",
        ],
        "issues": ["Workbook cell text goes to the provider (row cap and column projection limit it).", "Formulas are never calculated here, so totals and cached values may not exist in the file.", "Excel only; another platform needs its own reader."],
    },
    {
        "id": "investigate",
        "num": "3b",
        "name": "Investigations and coverage",
        "lane": "merge",
        "kind": "code",
        "status": "partial",
        "short": "Merges the three triggers into one list of stretches worth a look, and records which were actually examined.",
        "inputs": ["Screen events with contexts", "Steps, flags and their attached events", "The Checker's outcome per flag"],
        "outputs": ["investigations.json: time span, events, context, every trigger reason, flags, status, evidence ids", "A coverage summary on the SOP and in the report"],
        "model": "none",
        "prompts": [],
        "tree": [
            _s("Nominate stretches from three independent triggers", branches=[
                _b("A: an event that changed application, document, sheet or page, or a scene cut", "Candidate"),
                _b("B: a flag raised from what was said, with its attached events", "Candidate; kept even when no screen change was attached"),
                _b("C: an event no narrated step accounts for", "Candidate, even while the speaker talks about something else"),
            ]),
            _s("Merge candidates that share an event", "One investigation keeps every reason. A transcript flag with no screen change joins an overlapping investigation in the same context, else stands alone."),
            _s("After the Checker, set each investigation's status", branches=[
                _b("A Checker verdict exists, or a grounded observation covers its events", "examined"),
                _b("Its flag was left unchecked by the flag cap", "skipped_cap", WARN),
                _b("Its flag was left unchecked by the spend budget", "skipped_budget", WARN),
                _b("Nothing reached it", "unexamined", WARN),
            ]),
            _s("Render", "Section 3 of sop.md lists the spans not examined, and 'Nothing is missing' is replaced by a caveat while any remain."),
            _s("Retrieval library (not part of a run)", "core/screen/retrieve.py: context frame, crop, comparison, episode summary, wider interval, each with a stable evidence id, cached, charged to an inspection budget, refusing when it is spent. Used by tests and the dry run; the Checker's escalation stays deterministic."),
        ],
        "failure": "Non-fatal. On failure it records investigate_failed and the report carries no coverage section.",
        "knobs": ["INSPECT_MAX_IMAGES", "INSPECT_MAX_CROPS", "INSPECT_MAX_EXPANSIONS", "INSPECT_MAX_RETRIES_PER_EPISODE"],
        "measured": [
            "Real clip dry run, no model calls: 7 events became 7 investigations, 3 of them context transitions; with no transcript every event counts as unexplained.",
            "Trigger B and the examined/skipped statuses have been exercised on synthetic data only.",
        ],
        "issues": ["Investigations are per event; a flag adds its reason to each event it is attached to. An earlier version bridged them and produced one investigation spanning a whole real recording.", "The adaptive layer has not been compared with the fixed-frame or single-model baselines: they are written and tested but not run (they cost money)."],
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
    {"id": "X8", "title": "Speech is corroboration for a screen change, never a gate", "why": "Narration lagged the action by 4 to 5 seconds on the measured clip and some navigation was never narrated. A clue raises confidence; no clue on a real switch is itself a signal.", "rejected": "Treating an event as navigation only when the speaker names it; matching speech to the event's exact time.", "status": "built", "stages": ["surface"], "source": "core/screen/speech_clue.py"},
    {"id": "X9", "title": "A cap or budget cut is reported as 'not examined', never as 'nothing there'", "why": "The report printed 'Nothing is missing' even when flags were left unchecked. Coverage is recorded per investigation and the claim is withdrawn while any remain.", "rejected": "Silently dropping what the cap left over.", "status": "built", "stages": ["investigate", "outputs"], "source": "core/screen/investigate.py"},
    {"id": "X10", "title": "Observations, inferences and open questions stay separate, and a flag records why it exists", "why": "A screenshot can show that a value changed but not whether it was typed, pasted, recalculated or authorised. Each flag carries a finding kind (explained, observed but unexplained, unresolved requirement, potential conflict, unsupported assertion, no finding). A claim the screen does not show is 'unsupported', never 'disproved'.", "rejected": "Treating an absent demonstration as proof the step did not happen.", "status": "built", "stages": ["reconcile", "outputs"], "source": "report/build_spec.py"},
    {"id": "X12", "title": "Every question climbs a ladder: video, then document, then the person who recorded the process", "why": "Their time is the scarce resource, and a question asked after the evidence is sharp (a yes/no confirmation) instead of open. The recording shows values, not rules; the document holds rules. The person is still the last word on intent, because evidence shows what is, not what was meant.", "rejected": "Asking the person everything; letting the data pass overwrite the video's answer instead of recording a conflict.", "status": "built", "stages": ["checker", "document", "outputs"], "source": "core/ladder.py"},
    {"id": "X13", "title": "Every step that moves or edits data must answer four questions: source, key, target, effect", "why": "Underspecified steps ('identify the bill' with no criterion) are the gap this tool exists to find. A missing answer becomes a question by omission, like a missing transition, so it does not depend on the model noticing vague wording. A slot counts as said only with a verbatim transcript quote.", "rejected": "Hoping the Reviewer notices vague wording; a generic 'needs data' tag (it would be set on nearly everything).", "status": "built", "stages": ["reviewer", "reconcile", "document"], "source": "core/slots.py"},
    {"id": "X14", "title": "Screen events are classified by what changed in the application, never by how many pixels changed", "why": "A dropdown can change a third of the screen and mean nothing; one cell edit can change a fraction of a percent and be the process. Only positive proof drops an event (it came back, or it only scrolled); anything unclear is kept; every dropped event is listed with its reason.", "rejected": "A pixel or area threshold; ranking regions by size (ADR 0010).", "status": "built", "stages": ["surface"], "source": "core/screen/classify.py"},
    {"id": "X15", "title": "A selection is an anchor tied to speech and later values by time, confirmed only by two independent signals", "why": "A click on a cell may be what 'this' meant, or the source of a value pasted later. One signal makes it a candidate worded as 'possibly'; ambiguity (the value fits several changes) yields candidates, never a guess.", "rejected": "Dropping selections; treating every selection as a step.", "status": "built", "stages": ["surface", "reconcile"], "source": "core/screen/anchors.py"},
    {"id": "X16", "title": "Check that the workbook is the one in the recording before using it", "why": "A real run showed the supplied file had one total row where the recording had two, with different formulas. Answers drawn from a different file are about the wrong file, silently. The check compares the sheet count, the content of selected cells and the visible words, and a mismatch stops the workbook being used.", "rejected": "Trusting the file name; comparing only the sheet names.", "status": "built", "stages": ["document", "outputs"], "source": "core/data/match.py"},
    {"id": "X17", "title": "A number in a video claim must belong to a cell that depends on what the recording changed", "why": "The Checker read a real number (the billed total) and attached it to the wrong total one row below. Grounding the number in the evidence cannot catch that; knowing which cell it belongs to can. Totals are computed from the cells because the file stores no results.", "rejected": "Requiring only that a number appear in the evidence; asking a second model to review the first.", "status": "built", "stages": ["document", "checker"], "source": "core/data/guard.py"},
    {"id": "X18", "title": "'One shown, many needed' is found from the workbook and raised as a question", "why": "A person records the task once, for one item; the process is for every item. Nothing said or shown tells you so, but the range a formula reads does. The extent comes from the file; whether the same process applies to every item is theirs to confirm, so it is a question, not an added step.", "rejected": "Adding a 'repeat for each' step automatically; inferring repetition from the speech.", "status": "built", "stages": ["document", "outputs"], "source": "core/data/repeat.py"},
    {"id": "X19", "title": "The workbook decides which values are parameters; the real names stay, with a note about reuse", "why": "A recording is one instance of a process, so a flow copied from it says 'March'. A value is a parameter only if the file ties it to this run (typed into a formula). The sheet name 'Transactions - March' is kept as written and flagged as a potential future issue, because rewriting it would hide what is actually in the file. A result is drawn as a result because nobody performs it.", "rejected": "A model proposing parameter names; rewriting sheet names into placeholders; reporting every repeating column.", "status": "built", "stages": ["document", "outputs"], "source": "core/data/params.py"},
    {"id": "X11", "title": "No claim that the adaptive layer is cheaper or more accurate until it is measured against the baselines", "why": "Multi-stage design is not value by itself. A fixed-frame pipeline and a single-model call with the same evidence are written and tested but not run.", "rejected": "Reporting estimated savings as results.", "status": "planned", "stages": ["investigate", "outputs"], "source": "evals/compare_methods.py"},
]

DECISION_STAGES: Dict[str, List[str]] = {
    "D1": ["screen"], "D2": ["screen", "align"], "D3": ["input"], "D4": ["screen"], "D5": ["screen"], "D6": ["screen"],
    "D7": ["checker"], "D8": ["checker"], "D9": ["align"], "D10": ["writer"], "D11": ["reviewer"], "D12": ["align", "reconcile"],
    "D13": ["screen", "align"], "D14": ["reconcile"], "D15": ["screen"], "D16": ["input"], "D17": ["screen"], "D18": ["checker"],
    "D19": ["screen"], "D20": ["checker"],
    "ADR-0001": ["writer"], "ADR-0002": ["reviewer"], "ADR-0003": ["checker"], "ADR-0004": ["triage"],
    "ADR-0005": ["writer", "checker", "outputs"], "ADR-0006": ["screen"], "ADR-0007": ["checker"],
    "ADR-0008": ["reliability"], "ADR-0009": ["reviewer", "input"],
    "ADR-0010": ["screen", "reconcile", "checker"], "ADR-0011": ["investigate", "outputs"],
    "ADR-0012": ["investigate", "checker"], "ADR-0013": ["surface", "reconcile"],
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
    {"id": "Q4", "title": "Should events split when two actions fall inside one second?", "stages": ["screen", "reconcile"], "why": "Switching sheet and selecting a cell merged into one event, so 'copy' was mapped to nothing.", "options": ["Split on a changed-region jump (built behind SCREEN_SPLIT_ON_LOCUS_SHIFT, off by default, unmeasured)", "Shorten the settle window", "Leave merged and let the Reconciler read both"]},
    {"id": "Q5", "title": "How are web and ERP surfaces handled?", "stages": ["screen", "surface", "reconcile", "checker"], "why": "A generic web profile (URL, title, breadcrumb) now exists but has only synthetic tests. No web recording exists, and a web page change is not a large pixel change.", "options": ["Record a web walkthrough as a second eval case first", "Detect chrome regions from what never changes between events", "A hosted vision reading for unrecognised surfaces (sends screen images to a provider)"]},
    {"id": "Q6", "title": "Who confirms the event labels?", "stages": ["screen"], "why": "The draft labels were read off the detector's own keyframes, so a perfect score proves nothing about recall. A person has to watch the video.", "options": ["The recorder", "A reviewer on the engagement"]},
    {"id": "Q7", "title": "When does intake (outside context as questions, never answers) resume?", "stages": ["reviewer", "input"], "why": "It is the integration point for a discovery tool. Source ingestion is built; the intake prompt is not. Paused until the screen path is trusted.", "options": ["After the findings score is acceptable", "In parallel"]},
    {"id": "Q8", "title": "Will the workbook or other work file be available with the recording?", "stages": ["reconcile"], "why": "Why row 18 was chosen, and what the total formula depends on, are not on screen. Only the file recovers them (D14).", "options": ["Usually", "Sometimes", "Never"]},
    {"id": "Q9", "title": "How is client data protected at rest?", "stages": ["input", "reliability"], "why": "Runs and the cache are unencrypted; nothing is redacted before it is sent to the provider.", "options": ["Encrypted volume and scheduled cleanup (documented)", "Redaction before sending", "Local models only"]},
    {"id": "Q10", "title": "What combination of differences keeps tiny cell changes without flooding the index with cursor and scroll activity?", "stages": ["screen"], "why": "Thresholds were tuned on one clip. Region merging means a small edit is no longer dropped from a record, but whether it is detected at all still depends on SCREEN_ACTIVE_FRAC and the box floor.", "options": ["Region-level thresholds", "A structural-similarity score per region", "Per-recording calibration from the first seconds"]},
    {"id": "Q11", "title": "When does a context need another full frame?", "stages": ["surface"], "why": "A context is described once from the first frame seen. If the sheet is edited a lot, or the application changes a title without changing the surface, the stored labels go stale.", "options": ["Refresh on a changed locator only (current)", "Refresh after N events in a context", "Refresh when the labels' OCR differs"]},
    {"id": "Q12", "title": "How far should a transcript-triggered window expand when speech and action are seconds apart?", "stages": ["investigate", "checker"], "why": "Narration lagged by 4 to 5 seconds on one clip. The speech-clue window and Align's lead are separate fixed numbers, neither validated on a second recording.", "options": ["One shared, measured lag", "Estimate the lag per recording from context switches that were named aloud"]},
    {"id": "Q13", "title": "Would a model that chooses which retrieval tool to call beat the deterministic escalation?", "stages": ["checker", "investigate"], "why": "ADR 0007 rejected model-requested context for over-asking and unbounded cost, with no measurement either way. The retrieval library makes the experiment cheap to set up.", "options": ["Keep deterministic escalation", "Run the tool-loop experiment against it under an equal budget"]},
    {"id": "Q15", "title": "Does the document rung settle the questions the screen cannot (how rows are matched, what a total requires)?", "stages": ["document"], "why": "The profiler and tools work on the real workbook, but the model loop has only run against a fake client. Whether it cites the right formula cell and stops at 'candidates' where the data only suggests a rule is unmeasured.", "options": ["Run it on the real model against the labelled workbook questions", "Add a second workbook with a different layout first", "Compare with asking the person directly"]},
    {"id": "Q16", "title": "Are the event classes right on recordings other than the one they were built on?", "stages": ["surface"], "why": "Thresholds and the selection-versus-edit rule were set from one clip, and the labels are drafts a person has not confirmed. Web and ERP surfaces have never been seen.", "options": ["Label two more clips by hand and score the classes", "Calibrate thresholds per recording", "Add a web profile test recording"]},
    {"id": "Q14", "title": "Does the adaptive pipeline beat a single multimodal call given comparable evidence?", "stages": ["investigate", "outputs"], "why": "Neither the fixed-frame pipeline nor the single-model baseline has been run against the adaptive one. Until it is, there is no basis for saying the extra stages earn their cost.", "options": ["Run evals/compare_methods.py with credit, on the existing clip plus two new clips with predefined truth"]},
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
