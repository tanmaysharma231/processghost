# ProcessGhost: Architecture (v3)

Oct 10, 2026 · @Tanmay · revised Oct 11, 2026 after the Checker replay (`23-checker-learnings.md`) and a first screen-timeline build (`core/screen/`)

ProcessGhost v3 keeps the v2 transcript pipeline (Writer, Reviewer, Checker, Triage) and adds a deterministic screen timeline plus an alignment stage. This finds steps that are never spoken and cuts Checker cost by sending OCR text and small crops instead of full frames.

## Context and goals

Experts narrate processes on screen but skip steps they consider obvious. ProcessGhost turns a screen recording into an SOP and flags every place a newcomer would get stuck.

### Problem

The transcript only captures what is said: "click here", "paste it here", "total will update automatically". The screen holds the rest, and some steps are never mentioned at all.

### Constraints

- **Video only.** No click logs, keystrokes or input events. The source will eventually be a meeting bot recording screen share.
- **Live mode later.** The design must work on a rolling buffer so the batch run can become a live run without a rewrite.
- **Client data.** Recordings may contain KPMG or client data, so they cannot go through free hosted tiers.
- **Cost.** Vision calls dominate v2 cost; v3 must reduce them.
- **Honesty.** A fabricated resolution is worse than an open question. "Unresolvable" stays a valid outcome.

### Success criteria

- Silent steps (on screen, never spoken) appear as flags.
- Vague steps resolve to named targets where the screen shows them.
- Checker cost per flag drops versus v2 on the same recording.
- Missed and mislabelled steps are measured against a hand-labelled eval set.

## Current system (v2)

v2 is transcript-driven: every flag starts from what was said, and the screen is only checked inside windows the transcript points to. Reference: `21-pipeline-reference.md`.

| Stage | What it does | Kept in v3? |
| --- | --- | --- |
| 0 Transcription | Whisper or Sarvam Saaras v3, timed segments | Yes |
| 1 Writer | Naive SOP draft, vague wording preserved | Yes, unchanged |
| 2 Reviewer | Transitions with verbatim quotes, four ambiguity checks, flags | Yes, unchanged |
| merge\_reviewer\_output | Structural flag for any step pair with no grounded transition | Yes, extended by Align |
| 3 Checker | One call per flag over fixed-interval frames (vision) or OCR text | Yes, evidence and tiering change |
| 4 Triage | Pure function of status and severity | Yes, unchanged |

### Gaps v3 addresses

- **Silent steps are invisible.** A step that is never mentioned and leaves no hole in the narration never gets a flag.
- **Checker evidence is time-sampled, not change-driven.** Frames are taken every interval across a window, so most are redundant and the changed region is never isolated.
- **Checker dominates cost** at about $0.04 to 0.06 per flag on OpenAI vision.
- **Consequence claims** ("updates automatically") need an LLM call even when the screen plainly did or did not change.

## Target architecture

Transcript and screen run as two independent tracks and meet only at Align, which creates silent-step flags and attaches screen evidence before the Checker runs.

```
video.mp4 --+--> audio -> [0 Transcription] -> segments -> [1 Writer] -> [2 Reviewer] --+
            |                                                                          |
            +--> [0b Screen timeline] -> screen_events.json (events, scenes, keyframes)|
                          |                                                            v
                          +--------------------------------------------------> [2b Align] (new)
                                                                                       |
                                          flags (reviewer + coverage + screen-origin)  v
                                                                                [3 Checker] (evidence changed)
                                                                                       |
                                                                                       v
                                                                                [4 Triage] -> sop.md, build_spec.json
```
New: 0b, 2b. Changed: 3 (evidence and escalation only). Unchanged: 0, 1, 2, 4.

Writer, Reviewer and Triage are unchanged; the Checker keeps its schema and verdicts but gets change-driven crops and an OCR-first tier.

## Components

Two stages are new (0b Screen, 2b Align), one changes (3 Checker), the rest are unchanged from v2.

### 0 Transcription (unchanged)

- **In:** audio extracted with ffmpeg. **Out:** `segments_raw` `{id, start, end, text}`.
- Degraded timing sets `timing_degraded`; Align then skips time-based matching (see Failure behavior).

### 0b Screen timeline (new, plain Python, no LLM) -- built, `core/screen/`

- **Job:** turn frames into a list of discrete screen-change events.
- **In:** video. **Out:** `screen_events.json` (events, scenes, per-sample activity) plus full-resolution before/after keyframes on disk.
- **Steps (as implemented in `core/screen/timeline.py`):**
  1. Sample at `SCREEN_FPS` (3).
  2. Normalize: downscale to 960 px wide, grayscale, light blur (`diff.normalize_frame`).
  3. Absolute difference with the previous sample, per-pixel threshold, optional static mask, dilate, connected components, drop boxes under a minimum area (`diff.diff_boxes`).
  4. A sample is *active* only above `SCREEN_ACTIVE_FRAC` of changed pixels. Compression and cursor flicker sit around 0.0001 to 0.0002 on the recording measured so far; this threshold was tuned on that one clip.
  5. A run of active samples is one event; it ends after `SCREEN_SETTLE_S` (1 s) of stillness. A burst arriving after a still gap starts a new event.
  6. Keyframes: `before` = last sample before the change, `after` = the last active sample (the settled result). Both stored at full resolution.
  7. Event type, from the net before/after difference: `large_shift` if the after frame is the before frame shifted vertically (phase correlation plus a residual check); `scene_cut` if the changed boxes span at least `SCREEN_SCENE_COVERAGE` (30%) of the frame **and** at least `SCREEN_SCENE_MIN_CHANGED` (3%) of pixels changed; otherwise `local`.
  8. For `scene_cut` events, OCR the top and bottom strips of the after frame (local RapidOCR) as the scene label; scenes are the spans between scene cuts.
- **Why not changed-pixel fraction alone:** a spreadsheet sheet switch changed only about 10.8% of pixels, so the original "above 40% is a scene cut" rule never fires. Coverage plus a pixel floor separates a context switch from scattered small edits (a pasted cell plus the totals it feeds change a wide area but few pixels).
- **Known limits:** two quick actions inside one second become one event (on the real clip, "switch sheet" and "select and copy a cell" are one event). Moving-region splitting is not built. Web/ERP behavior is untested; the only recording so far is a spreadsheet.
- Runs over the whole video, in parallel with transcription and the Writer. No LLM calls happen here (D17).

### 1 Writer (unchanged)

Naive SOP draft from the whole transcript. Keeps vague wording so later stages can find the gaps. Fatal on failure.

### 2 Reviewer (unchanged)

Transitions grounded in verbatim quotes, four ambiguity checks, flags with severity and segment window. Non-fatal.

### 2b Align (new, plain Python plus one optional call; extends `merge_reviewer_output`) -- deterministic layer built, `core/screen/align.py`

- **Job:** join screen events to transcript segments and steps.
- **Why not a plain time window:** real segments are about 6 s long (the clip's four segments span 0 to 7.2, 7.2 to 12.0, 12.0 to 20.2 and 20.2 to 21.7 s), so a +/-3 s window matches almost every event. Narration also lags the action: the copy happens near 10 s but "I copy it" is spoken from 12 s.
- **Silent-step detection, two layers:**
  1. **Event density, in code.** For each step, take its time span (first to last attributed segment) and count events near it, relative to the previous and next events. Candidates are events that no step's span covers, and surplus events inside one step's span beyond what that step describes. Event type does not gate this: a scroll or filter that nothing mentions is a candidate.
  2. **If density is inconclusive,** the model gets the candidate event's crops and may request the adjacent set (earlier or later) once before deciding whether it is a separate step. An event that stays unmapped becomes a structural flag with `origin: screen`.
- **Evidence attachment:** events inside an existing flag's window (plus a lead of a few seconds, since action precedes speech) get their ids attached to that flag.
- **Consequence shortcut:** for an "unverified consequence" flag, no `local` event after the action is a `contradicted` candidate; a matching event becomes Checker evidence.
- **Said-but-nothing-visible:** step segments with no nearby event are recorded as a note, not a flag.
- **Noise control:** merge adjacent events, drop tiny ones, dedupe silent flags against Reviewer flags covering the same step pair, so `MAX_CHECKED_FLAGS` (20) is not spent on noise.

### 3 Checker (evidence and escalation change, schema unchanged) -- built, `core/screen/evidence.py` + `core/pipeline.py`

- **Evidence per flag, always both context and detail:**
  1. a full after-frame downscaled to about 1024 px wide (which sheet, file or page you are on is only visible here);
  2. stitched numbered tile pairs: padded before/after crops of each changed region at full resolution (padding includes row and column headers);
  3. OCR text (local RapidOCR) of the chrome strips: title, tab, URL, status bar;
  4. a low-resolution before-frame when the event is a context switch.
- **Tier 1, OCR + text model:** the existing OCR-text reasoning path, for both providers (OCR is now local, not the hosted Sarvam job).
- **Tier 2, vision:** only when tier 1 returns `unresolvable` and the question needs visual layout (scroll vs filter, which item is highlighted).
- **Fallback:** flags with no attached event use v2 `extract_frames_window`.
- **Prompt change:** describe the tile layout; keep the observe-then-decide procedure and verdict rules; add an anti-echo rule (see `18-known-gaps-v2.md` entry 5): `observed` must contain something not present in the prompt, and a high-echo observation is treated as `unresolvable`.
- `CheckerOutput` and resolution behavior (inferred clauses, inserted inferred steps) are unchanged, so silent-step flags flow through existing machinery.

#### Escalation (D18, D20)

Deterministic, never driven by the model's own request for more:

1. **Level 0:** the event nearest the flag window, with the evidence above.
2. **Level 1:** if the verdict is `unresolvable`, retry once with the surrounding events (plus or minus 2) and the scene labels for that window as text.
3. **Stop.** A flag still `unresolvable` goes to the recorder as a question. Maximum 2 calls per flag.

The model is not asked whether it needs more context: self-report is what D11 rejects, and models tend to ask for more or claim to be done.

### 4 Triage (unchanged)

Pure function of flag status and severity. Silent-step flags triage like any structural flag.

### Optional input: work file

If the .xlsx comes with the recording, a before/after diff with openpyxl gives formulas and values the screen never shows. It attaches to consequence flags as evidence. Planned alongside the paused intake stage, not in the first v3 build.

## Architecture decisions

Twenty decisions shape v3 (D7, D9 and D20 revised on Oct 11 after real-run evidence); D1 to D9 and D17 to D20 are new, D10 to D16 confirm or extend v2 choices.

| ID | Decision | Why | Rejected alternatives |
| --- | --- | --- | --- |
| D1 | Treat screen recordings as change points plus screenshots, not general video | Screens are static between discrete jumps, so "find when it changed, then read a few images" captures nearly all information | Native video VLM: most expensive, weakest at small screen text, and most "open video models" are generation models, not understanding models |
| D2 | Build speech and screen timelines independently, then join on time | Only an independent screen timeline catches steps that are never spoken | Transcript decides where to look (v2, or an agent calling `look(t)`): misses silent steps by design |
| D3 | Assume video only, no input events | Target source is a meeting bot; recordings will not come with click or keystroke logs | Recorder with event logging: simplest and most accurate, but not available in the target setting |
| D4 | Changed region is the primary locator; cursor is a hint | Many steps are not clicks (typing, shortcuts, scroll), results often appear away from the cursor, and cursor detection is fragile across themes and compression | Crop around the mouse (original idea): misses dialogs, keyboard actions and remote effects |
| D5 | Diff by thresholded pixels and connected components, at 2 to 5 fps | Catches a single cell changing; mean frame difference would miss it. Screens do not change faster than a few fps | Full 30 fps or mean-difference scoring: wasted compute, missed small changes |
| D6 | One before/after keyframe pair per event (last static, first settled) | One pair captures the whole change regardless of event length; avoids sending 100+ frames | Fixed-interval frames per window (v2 Checker): redundant frames, change never isolated |
| D7 | Send a full downscaled after-frame, stitched padded before/after crops of the changed regions, and OCR'd chrome text | VLM cost scales with pixels; crops cut cost, but only the full frame and the chrome (title, tabs, status bar) say which sheet, file or page you are on | Full frames only: costly. Crops only: lose the sheet/file/page context (confirmed on real runs) |
| D8 | OCR first, vision only on escalation | Screens are text-heavy, so OCR is effectively the bounding-box detector; small dedicated OCR models beat large VLMs on documents | Generic YOLO detection: trained on photos, does not know UI elements. Vision on every flag: v2 cost problem |
| D9 | Attach screen events to existing flags using a window of about plus or minus 3 s, with a longer lead before the flag | Speakers narrate before, during or after an action; on the real clip the copy happens about 2 s before it is spoken | Exact timestamp match: misaligns most vague phrases. Not used for silent-step detection (see 2b): segments are about 6 s long, so a 3 s window matches almost everything |
| D10 | Keep the Writer naive (v2) | A repaired step erases the gap the later stages exist to find | Writer infers specifics: hides gaps |
| D11 | Keep gap detection by omission and code-checked quotes (v2) | Model self-reports of "ok/broken" are unreliable; absence of a grounded transition is verifiable | Ask the model to rate each step |
| D12 | Silent steps enter as structural flags, reusing Checker and inserted inferred steps | No new resolution path; Triage and outputs work unchanged | Separate silent-step pipeline: duplicate logic |
| D13 | Screen stage and Align are non-fatal with fallback to v2 evidence | A broken diff must not lose the run; v2 behaviour is a known baseline | Make screen stage required |
| D14 | Work file is the source of truth for hidden logic; video gives order and intent | Formulas and auto-updates never appear on screen unless the cell is selected; no CV can recover undisplayed information | Infer formulas from screen: impossible in general |
| D15 | Every stage works on a short rolling buffer | Live meeting mode later needs only "feed frames as they arrive", results a few seconds behind | Whole-video batch algorithms: rewrite needed for live |
| D16 | Client recordings use local or contracted models only; free hosted tiers for public or personal material | Free tiers may use requests for model training | Free hosted APIs for everything: data risk |
| D17 | Precompute the diff index for the whole video; call OCR and VLM only on demand | Diffing is free and takes a minute or two; it is the only way to find silent steps and makes expansion a lookup. Model calls are the cost, so they run only where a flag needs them | Diff only around Reviewer flags: misses silent steps and re-scans frames on every expansion. Model calls on every event: pays for steps already explained |
| D18 | Start the Checker at the nearest event and escalate once: plus or minus 2 events, then stop | Most flags settle on one event; widening only when it is unresolved keeps cost low (max 2 calls per flag) while still reaching context a few switches away | Fixed plus or minus 10 s window: may hold zero or twenty changes. Always send the whole scene: costly for simple flags. A third, whole-scene level: more cost for little evidence it helps (revisit if level 1 leaves many flags unresolvable) |
| D19 | Pass scene labels (OCR of tab or title per scene) as text for the surrounding window | Multiple screen switches carry meaning (where data came from, where it went); text conveys the sequence at a fraction of the cost of images | Send a frame per scene switch: costly and mostly redundant |
| D20 | Escalate deterministically: level 0 (nearest event), then once at plus or minus 2 events if `unresolvable`, then stop; no `needs_more_context` verdict | The model asking for more is self-report (D11): it over-asks or claims to be done. A fixed rule caps cost at 2 calls per flag and leaves `CheckerOutput` unchanged | Model-driven `needs_more_context` with a direction: smarter targeting but unreliable, a schema change and up to 3 calls per flag. Never expand: loses answers just outside the first window |

"Unresolvable" stays a valid Checker verdict (v2 rule); every decision above assumes a fabricated answer is worse than an open question.

## Data contracts

v3 adds one artifact (`screen_events.json`) with scene records, optional flag fields and one new non-terminal Checker verdict; existing Pydantic schemas otherwise stay as they are.

### ScreenEvent (`screen_events.json`, `core/models/screen.py`)

```json
{
  "event_id": "ev-005",
  "t_start": 9.7,
  "t_end": 10.0,
  "type": "scene_cut",
  "changed_pct": 10.99,
  "coverage_pct": 77.8,
  "bboxes": [[412, 288, 96, 22]],
  "before_frame": "keyframes/t00009333.png",
  "after_frame": "keyframes/t00010000.png",
  "scene_label": "Arial | 10pt | ... | Find | ..."
}
```

- `type`: `local` | `large_shift` (scroll) | `scene_cut` (context switch).
- `bboxes`: `[x, y, w, h]` in original video pixels. Keyframes are full resolution.
- `changed_pct`: share of the downscaled frame whose pixels changed, before vs after.
- `coverage_pct`: bounding box of all changed boxes as a share of the frame.
- `scene_label`: OCR of the top and bottom strips, set for `scene_cut` only.
- `ScreenTimeline` also carries `scenes` and a per-sample `activity` trace `(t_s, changed_frac, n_boxes)` used for plotting and threshold tuning.

### Alignment record (in `run.json`)

```json
{
  "event_id": "ev-007",
  "segment_ids": [4],
  "step_ids": ["step-05"],
  "bucket": "vague"
}
```

- `bucket`: `explained` | `vague` | `silent` | `said_no_event`.

### Flag extensions (`core/models/sop.py`)

| Field | Type | Meaning |
| --- | --- | --- |
| `origin` | `reviewer` \| `coverage` \| `screen` | Which stage created the flag |
| `event_ids` | list of str, optional | Screen events attached as Checker evidence |
| `checker_tier` | `ocr` \| `vision` \| `fallback_frames`, optional | Which evidence path produced the verdict |

All three are optional, so v2 runs and `build_spec.json` consumers keep working.

### Scene record (in `screen_events.json`)

```json
{
  "scene_id": "sc-3",
  "t_start": 7.2,
  "t_end": 14.6,
  "label": "Transactions",
  "event_ids": ["ev-3", "ev-4", "ev-5"]
}
```

### CheckerOutput

Unchanged. The only addition is `expansion_level` on the flag (0 or 1): the level at which the final verdict was reached.

## Failure behavior

Only the Writer remains fatal; every new stage degrades to v2 behaviour.

| Stage | On failure | Degrades to | Visible as |
| --- | --- | --- | --- |
| 0 Transcription, timing lost | `timing_degraded` set | Align skipped; no silent flags, no event attachment | Run banner, metrics |
| 0b Screen timeline | Empty event list | v2 behaviour: no silent flags, Checker uses interval frames | `screen_failed` in metrics, `trace.jsonl` |
| 1 Writer | Run aborts (`WriterFailedError`) | None | Exception |
| 2 Reviewer | Writer doc ships, zero Reviewer flags | Align still adds screen-origin flags | "REVIEW FAILED" banner |
| 2b Align | No alignment records | Reviewer and coverage flags only | `align_failed` in metrics |
| 3 Checker tier 1 (OCR) | Tier skipped | Tier 2 vision, then fallback frames | `checker_tier` on flag |
| 3 Checker, one flag | Flag becomes `check_failed` | Run continues | Flag status |
| 4 Triage | Cannot fail on valid input | n/a | n/a |
| 3 Checker, level 1 still unresolvable | Stops after 2 calls | Flag stored as unresolvable, goes to the recorder as a question | expansion\_level on flag, trace.jsonl |

### Known degradation risks

- **Noisy diffs** (compression, animations) create false events and false silent flags. Mitigation: size and duration thresholds, overlay masks, dedupe against Reviewer flags, severity `note` for low-confidence screen flags.
- **Staged actions** (dialog opens, filled, closed) can make before and after look identical. Mitigation: split events when the changed region moves; keep one middle keyframe.

## Cost and performance

The screen stage is local and effectively free; savings come from Checker calls moving to OCR text and crops. Figures below are rough estimates for a 30-minute recording, to be replaced with measured numbers.

| Item | Estimate | Cost |
| --- | --- | --- |
| Frames diffed locally at 3 fps | \~5,400 | Free, a minute or two on a laptop |
| Screen events | \~50 to 150 | Free |
| Events in vague or silent buckets | \~10 to 40 | Feed flags, capped at 20 checked |
| Checker tier 1 (OCR + text model) | Most checked flags | Text-model tokens only |
| Checker tier 2 (vision on stitched crops) | Escalations only | One small image per flag |

### Levers

- Crop and downscale before any vision call; cost scales with pixels, not calls.
- Cache OCR and verdicts by frame hash, since screens repeat.
- Escalate in tiers: OCR, then a small local VLM, then a hosted VLM only for hard cases.

### Measure

Run v2 and v3 on the same recordings and compare cost per flag, verdict mix (resolved, contradicted, unresolvable) and wall-clock time. The v2 baseline is about $0.04 to 0.06 per flag on OpenAI vision: up to 16 full frames per call, downscaled to 1024 px. On the five measured runs, 68% of the frame-to-frame differences in those bundles were tiny (cursor, caret, status bar) and 15 of 38 flags failed on rate limits (`23-checker-learnings.md`).

## Model and hosting options

Default to small local models for client recordings; hosted free tiers are for prototyping on public or personal material only (D16). Versions move fast, so pick the newest release in each family when building.

| Role | Local options | Hosted options | Notes |
| --- | --- | --- | --- |
| OCR on crops, chrome strips and title bars | **RapidOCR (default, installed; PP-OCR on ONNX, pip-only, no system binary, works on Windows, local)**; alternatives: PaddleOCR-VL (~0.9B), GLM-OCR (~0.9B), Granite-Docling (~0.26B) | Mistral OCR, OCR.space (plain text, 1MB limit) | Small dedicated OCR models lead OmniDocBench ahead of much larger VLMs. Measured: RapidOCR reads the status-bar page/sheet name and sheet count on a 1080p frame in about 1 to 3 s per strip |
| Checker tier 1 text reasoning | Small local LLM | Current `MODEL_TEXT` / Sarvam | Existing OCR-text path |
| Checker tier 2 vision | Qwen-VL small (2B to 4B, quantized), SmolVLM2 (\~2B) | Current `MODEL_VISION`, Gemini Flash free tier, OpenRouter free vision models | Free tiers may train on requests |
| UI element hints (optional) | OmniParser, UI-TARS, ShowUI | n/a | Only if OCR misses unlabeled icons |
| Transcription | Whisper (local) | Whisper API, Sarvam Saaras v3 | Unchanged from v2 |

Free hosted limits change without notice (Gemini and Mistral no longer publish free-tier limits), so treat them as prototyping tools, not dependencies.

## Worked example: Monthly Actuals recording (measured)

Real clip `data/english_hackathon_test.mp4` (24 s, 1920x1080). Transcript and screen events are measured (a Whisper run from 2026-08-30 and `scripts/screen_timeline.py`), not assumed. Alignment and flags below are what the design predicts, not yet produced by code.

### Transcript (Stage 0)

| Seg | Time (s) | Speech |
| --- | --- | --- |
| 0 | 0.0 to 7.2 | "So, we have the master files here, what we do is we go to monthly actions, based on what" |
| 1 | 7.2 to 12.0 | "value we have, we fill the actualization, taking the data from transactions." |
| 2 | 12.0 to 20.2 | "So, Amazon Web Services, I copy it, go to monthly actions, I paste it here, and the" |
| 3 | 20.2 to 21.7 | "total will get updated automatically." |

### Screen events (Stage 0b, measured)

| Event | Time (s) | Type | What changed (read from the keyframes) |
| --- | --- | --- | --- |
| ev-001 | 3.0 | local | A cell is selected on the first sheet |
| ev-002 | 4.3 to 5.3 | scene_cut | Switch to the Monthly Actuals sheet |
| ev-003 | 6.3 | local | Selection moves on Monthly Actuals |
| ev-004 | 8.3 | local | Another selection change on Monthly Actuals |
| ev-005 | 9.7 to 10.0 | scene_cut | Switch to the Transactions sheet **and** select and copy one cell (the dashed copy border is present by 10.5 s) |
| ev-006 | 16.0 to 16.3 | scene_cut | Back to Monthly Actuals |
| ev-007 | 18.3 | local | A value is pasted into one cell; totals elsewhere on the sheet change |

Between 10.5 s and 15.8 s the screen is static apart from the mouse cursor, so no event is correct there.

### What the design predicts (Stages 2 and 2b)

| Observation | What it shows | Expected flag |
| --- | --- | --- |
| Segment 0 spans 3 events (ev-001 to ev-003) but names two actions | Surplus events inside one step's span | Candidate silent step (density rule) |
| ev-005 contains a copy two seconds before "I copy it" is spoken | Narration lags the action | Evidence attaches to the "copy" flag with a lead before the flag window; the sheet switch is one event with the copy, which is the known merge limit |
| ev-007 shows a pasted cell and changed totals | The "updates automatically" claim is visible on screen | Consequence shortcut: evidence that something changed; the formula behind it is not on screen |
| Scene labels | Status-bar text carries the page/sheet name (for example a page style containing the sheet name) | Tier 1 (OCR) can name the sheets without a vision call |

### Questions that go back to the recorder

- Which sheet is "master files"? Which cell is selected first, and why?
- How are rows matched between sheets (the name spoken versus the key on screen)?
- What formula produces the total that updates after the paste, and is it truly automatic?

## Risks and open questions

The biggest risk is unreliable event detection on compressed meeting recordings, so it is tested first on real data.

### Risks

- **Meeting recording quality:** low resolution, variable frame rate and compression blur small text and create false diffs.
- **Overlays differ by tool:** Teams, Zoom and Meet each place thumbnails, banners and captions differently; masks must be per tool or detected.
- **Flag flooding:** too many screen-origin flags could crowd out Reviewer flags under the 20-flag cap.
- **Hidden logic:** formulas, auto-fill and backend rules never appear on screen; only the work file recovers them.
- **Run-to-run variance:** v2 already sees the structural vs ambiguity split vary on identical input (`18-known-gaps-v2.md` entry 1); screen flags must be deduped deterministically so they do not add variance.
- **Window switches with multiple monitors** or partial screen share may look like scene cuts that are not process steps.

### Open questions

- Will the work file usually be available with the recording?
- Which meeting tool must be supported first?
- Should screen-origin flags count toward `MAX_CHECKED_FLAGS`, or get their own budget?
- Which local OCR and VLM run acceptably on the target hardware?
- Should the Reviewer receive a short event summary, or stay transcript-only to keep D2's independence?
- How should per-tool overlays (Teams, Zoom, Meet thumbnails and banners) be masked: configured per tool, or detected from regions that never relate to the shared screen?
- Two quick actions inside the settle window merge into one event (select-and-copy right after a sheet switch). Is the Checker's tile layout enough to describe both, or should events split on a changed-region jump?

## Roadmap

Build in increments, each with an exit check before the next starts. Labels and scoring come before any change to the Checker.

| # | Increment | Status | Exit criteria |
| --- | --- | --- | --- |
| 1 | 0b Screen timeline (`core/screen/`, `scripts/screen_timeline.py`) | Built, unit-tested on synthetic videos, run on the real clip: 7 events | Precision and recall against hand-labelled event times (`evals/score_events.py`), labels confirmed by a human; false and missed events listed |
| 1b | Second eval case: a web/ERP walkthrough | Not started; needs a recording | Same scoring; context switches and scrolls classified correctly on pages, not only sheets |
| 2 | 2b Align, silent-step flags only | Deterministic layer built (`core/screen/align.py`, 9 tests), opt-in via `run_pipeline(use_screen=True)`; the optional model layer is not built. On the real clip it raised no silent flag (the Reviewer's flags already cover every event), so it is unproven | Silent steps appear as screen-origin flags; no duplicates of Reviewer flags; cost of the optional model call measured |
| 3 | Checker evidence: full frame + crops + OCR tier, vision escalation, deterministic 2-level escalation, anti-echo rule | Built (`core/screen/evidence.py`, `_check_flag_with_events`, `CHECKER_EVENT_SYSTEM`, 14 tests). One real run: $0.137 total, 0 failed flags, but several weak "resolved" answers (see `23-checker-learnings.md`) | Same or better verdicts than v2 on the same recordings, lower cost per flag, echo score down |
| 4 | Event attachment to Reviewer flags, consequence shortcut | Attachment built (events inside a flag's window, or up to 3 s before it); the consequence shortcut is not built | Consequence flags resolved or contradicted without a vision call where the screen is clear |
| Later | Work file diff, live buffer mode, per-tool overlay masks | | Defined after increment 4 results |

### Eval set

- [x] Draft labels for the spreadsheet clip (`evals/cases/bills_tracker/expected_events.json`). They were read off the detector's own keyframes, so a score of 1.00 against them proves nothing about recall.
- [ ] A human watches the full clip, corrects the labels, adds anything missed and sets `confirmed: true`.
- [ ] Add a web/ERP recording as a second case.
- [ ] Record the v2 baseline: flags, verdicts, cost, missed steps (partly done: `23-checker-learnings.md`).
- [ ] Re-run after each increment and compare.
