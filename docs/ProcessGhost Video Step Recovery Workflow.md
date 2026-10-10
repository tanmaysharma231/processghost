# ProcessGhost: Architecture (v3)

Oct 10, 2026 · @Tanmay

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

&#91;embedded content: v3 run · 2 new stages, 1 changed\]

Writer, Reviewer and Triage are unchanged; the Checker keeps its schema and verdicts but gets change-driven crops and an OCR-first tier.

## Components

Two stages are new (0b Screen, 2b Align), one changes (3 Checker), the rest are unchanged from v2.

### 0 Transcription (unchanged)

- **In:** audio extracted with ffmpeg. **Out:** `segments_raw` `{id, start, end, text}`.
- Degraded timing sets `timing_degraded`; Align then skips time-based matching (see Failure behavior).

### 0b Screen timeline (new, plain Python, no LLM)

- **Job:** turn frames into a list of discrete screen-change events.
- **In:** video. **Out:** `screen_events.json` plus keyframes on disk.
- **Steps:**
  1. Sample frames at 2 to 5 fps.
  2. Normalize: downscale, grayscale, slight blur against compression noise.
  3. Absolute difference with the previous frame; threshold per pixel.
  4. Mask fixed overlays (meeting thumbnails, banners, clock) and the cursor.
  5. Dilate, take connected components as boxes, drop boxes under a minimum size.
  6. Classify: `local` (small boxes), `large_shift` (scroll or filter), `scene_cut` (above roughly 40% changed).
  7. Group changes close in time into one event; the event ends when the screen is static for about 1 s.
  8. Keyframes: `before` = last static frame before the event, `after` = first settled frame after it. Split the event if the changed region moves mid-event.
  9. For scene cuts, OCR the title bar and tab strip to name the new window or sheet. Events between two scene cuts form a scene carrying that label.
- Runs over the whole video, in parallel with transcription and the Writer, so the index is ready when the Reviewer finishes. No LLM calls happen here (D17).

### 1 Writer (unchanged)

Naive SOP draft from the whole transcript. Keeps vague wording so later stages can find the gaps. Fatal on failure.

### 2 Reviewer (unchanged)

Transitions grounded in verbatim quotes, four ambiguity checks, flags with severity and segment window. Non-fatal.

### 2b Align (new, plain Python, extends `merge_reviewer_output`)

- **Job:** join screen events to transcript segments and steps on time.
- **Window:** an event matches segments from about 3 s before to 3 s after it.
- **Outputs:**
  1. **Silent-step flags:** an event of type `local` or `scene_cut` with no matching step segment (only appendix segments or silence) becomes a structural flag with `origin: screen`, `between_step_ids` from the surrounding steps, and the event id attached.
  2. **Evidence attachment:** events inside an existing flag's window get their ids attached to that flag.
  3. **Consequence shortcut:** for an "unverified consequence" flag, no `local` event after the action is pre-marked as a `contradicted` candidate; a matching event becomes Checker evidence.
  4. **Said-but-nothing-visible:** step segments with no nearby event are recorded as a note for review, not a flag.
- **Noise control:** merge adjacent events, drop events below a size threshold, dedupe silent flags against Reviewer flags covering the same step pair, so `MAX_CHECKED_FLAGS` (20) is not spent on noise.

### 3 Checker (evidence and tiering change, schema unchanged)

- **Evidence per flag:** for each attached event, padded before/after crops of every changed region (padding includes row and column headers), stitched as numbered tile pairs, plus a small low-res overview of the after frame with boxes drawn. OCR of title bar and nearby headers goes in as text.
- **Tier 1, OCR + text model:** OCR the crops and run the existing OCR-text reasoning path, for both providers.
- **Tier 2, vision:** only when tier 1 returns `unresolvable` and the question needs visual layout (scroll vs filter, which item is highlighted).
- **Fallback:** flags with no attached event use v2 `extract_frames_window`.
- **Prompt change:** describe the tile layout; keep the observe-then-decide procedure and verdict rules as they are.
- `CheckerOutput` and resolution behavior (inferred clauses, inserted inferred steps) are unchanged, so silent-step flags flow through existing machinery.

#### Expansion loop (D18 to D20)

The Checker starts with the least evidence and widens only when it says it needs more. Expansion is by events from the precomputed index, never by raw seconds.

1. **Level 0:** the event nearest the flag window. Padded before/after crops, OCR tier first.
2. **Level 1:** ±2 events around it, added as more numbered tiles.
3. **Level 2:** the whole scene, from the previous scene cut to the next one.
4. **Stop** at `resolved` or `contradicted`, or when the budget (3 levels, or a tile cap) is spent. A flag still unsettled then becomes `unresolvable`.

At every level, scene labels for the surrounding window go in as text (for example "Transactions sheet, then Monthly Actuals sheet"), so the Checker knows where it is without seeing every screen switch as an image.

The Checker returns `needs_more_context` with a direction (`earlier`, `later`, `both`) to trigger the next level. `unresolvable` is reserved for "the answer is not on screen at all", which ends the loop at once.

### 4 Triage (unchanged)

Pure function of flag status and severity. Silent-step flags triage like any structural flag.

### Optional input: work file

If the .xlsx comes with the recording, a before/after diff with openpyxl gives formulas and values the screen never shows. It attaches to consequence flags as evidence. Planned alongside the paused intake stage, not in the first v3 build.

## Architecture decisions

Twenty decisions shape v3; D1 to D9 and D17 to D20 are new, D10 to D16 confirm or extend v2 choices.

| ID | Decision | Why | Rejected alternatives |
| --- | --- | --- | --- |
| D1 | Treat screen recordings as change points plus screenshots, not general video | Screens are static between discrete jumps, so "find when it changed, then read a few images" captures nearly all information | Native video VLM: most expensive, weakest at small screen text, and most "open video models" are generation models, not understanding models |
| D2 | Build speech and screen timelines independently, then join on time | Only an independent screen timeline catches steps that are never spoken | Transcript decides where to look (v2, or an agent calling `look(t)`): misses silent steps by design |
| D3 | Assume video only, no input events | Target source is a meeting bot; recordings will not come with click or keystroke logs | Recorder with event logging: simplest and most accurate, but not available in the target setting |
| D4 | Changed region is the primary locator; cursor is a hint | Many steps are not clicks (typing, shortcuts, scroll), results often appear away from the cursor, and cursor detection is fragile across themes and compression | Crop around the mouse (original idea): misses dialogs, keyboard actions and remote effects |
| D5 | Diff by thresholded pixels and connected components, at 2 to 5 fps | Catches a single cell changing; mean frame difference would miss it. Screens do not change faster than a few fps | Full 30 fps or mean-difference scoring: wasted compute, missed small changes |
| D6 | One before/after keyframe pair per event (last static, first settled) | One pair captures the whole change regardless of event length; avoids sending 100+ frames | Fixed-interval frames per window (v2 Checker): redundant frames, change never isolated |
| D7 | Send stitched, padded before/after crops plus an overview thumbnail and OCR'd context | VLM cost scales with pixels; crops cut cost while thumbnail and headers keep spatial context | Full frames: costly. Bare crops: lose labels such as column headers and sheet names |
| D8 | OCR first, vision only on escalation | Screens are text-heavy, so OCR is effectively the bounding-box detector; small dedicated OCR models beat large VLMs on documents | Generic YOLO detection: trained on photos, does not know UI elements. Vision on every flag: v2 cost problem |
| D9 | Alignment window of about ±3 s | Speakers narrate before, during or after an action | Exact timestamp match: misaligns most vague phrases |
| D10 | Keep the Writer naive (v2) | A repaired step erases the gap the later stages exist to find | Writer infers specifics: hides gaps |
| D11 | Keep gap detection by omission and code-checked quotes (v2) | Model self-reports of "ok/broken" are unreliable; absence of a grounded transition is verifiable | Ask the model to rate each step |
| D12 | Silent steps enter as structural flags, reusing Checker and inserted inferred steps | No new resolution path; Triage and outputs work unchanged | Separate silent-step pipeline: duplicate logic |
| D13 | Screen stage and Align are non-fatal with fallback to v2 evidence | A broken diff must not lose the run; v2 behaviour is a known baseline | Make screen stage required |
| D14 | Work file is the source of truth for hidden logic; video gives order and intent | Formulas and auto-updates never appear on screen unless the cell is selected; no CV can recover undisplayed information | Infer formulas from screen: impossible in general |
| D15 | Every stage works on a short rolling buffer | Live meeting mode later needs only "feed frames as they arrive", results a few seconds behind | Whole-video batch algorithms: rewrite needed for live |
| D16 | Client recordings use local or contracted models only; free hosted tiers for public or personal material | Free tiers may use requests for model training | Free hosted APIs for everything: data risk |
| D17 | Precompute the diff index for the whole video; call OCR and VLM only on demand | Diffing is free and takes a minute or two; it is the only way to find silent steps and makes expansion a lookup. Model calls are the cost, so they run only where a flag needs them | Diff only around Reviewer flags: misses silent steps and re-scans frames on every expansion. Model calls on every event: pays for steps already explained |
| D18 | Start the Checker at the nearest event and expand: ±2 events, then the whole scene, then stop | Most flags settle on one event; widening only when needed keeps cost low while still reaching context several switches away | Fixed ±10 s window: may hold zero or twenty changes. Always send the whole scene: costly for simple flags |
| D19 | Pass scene labels (OCR of tab or title per scene) as text for the surrounding window | Multiple screen switches carry meaning (where data came from, where it went); text conveys the sequence at a fraction of the cost of images | Send a frame per scene switch: costly and mostly redundant |
| D20 | Add a non-terminal needs\_more\_context verdict with a direction; keep unresolvable terminal | v2 unresolvable mixes "not shown enough" with "not on screen at all"; expansion only works if the Checker can tell them apart and say which way to look | Expand on every unresolvable: wastes calls on true dead ends. Never expand: loses answers just outside the first window |

"Unresolvable" stays a valid Checker verdict (v2 rule); every decision above assumes a fabricated answer is worse than an open question.

## Data contracts

v3 adds one artifact (`screen_events.json`) with scene records, optional flag fields and one new non-terminal Checker verdict; existing Pydantic schemas otherwise stay as they are.

### ScreenEvent (`screen_events.json`)

```json
{
  "event_id": "ev-007",
  "t_start": 15.8,
  "t_end": 16.9,
  "type": "local",
  "changed_pct": 0.4,
  "bboxes": [[412, 288, 96, 22], [412, 610, 96, 22]],
  "before_frame": "frames/f_0031.png",
  "after_frame": "frames/f_0035.png",
  "scene_label": null
}
```

- `type`: `local` | `large_shift` | `scene_cut`.
- `bboxes`: `[x, y, w, h]` in pixels of the downscaled frame.
- `scene_label`: OCR of title bar or tab strip, set for `scene_cut` only.

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

### CheckerOutput extension

| Field | Change | Meaning |
| --- | --- | --- |
| `verdict` | adds `needs_more_context` | Non-terminal; triggers the next expansion level |
| `direction` | new, optional | `earlier` \| `later` \| `both`, set only with `needs_more_context` |
| `expansion_level` | new on the flag | 0, 1 or 2: the level at which the final verdict was reached |

`needs_more_context` never reaches Triage; once the budget is spent the flag is stored as `unresolvable`.

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
| 3 Checker, expansion budget spent | Stops expanding | Flag stored as unresolvable, goes to the recorder as a question | expansion\_level on flag, trace.jsonl |

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

Run v2 and v3 on the same recordings and compare cost per flag, verdict mix (resolved, contradicted, unresolvable) and wall-clock time. The v2 baseline is about $0.04 to 0.06 per flag on OpenAI vision.

## Model and hosting options

Default to small local models for client recordings; hosted free tiers are for prototyping on public or personal material only (D16). Versions move fast, so pick the newest release in each family when building.

| Role | Local options | Hosted options | Notes |
| --- | --- | --- | --- |
| OCR on crops and title bars | PaddleOCR-VL (\~0.9B), GLM-OCR (\~0.9B), Granite-Docling (\~0.26B), classic PP-OCR | Mistral OCR, OCR.space (plain text, 1MB limit) | Small dedicated OCR models lead OmniDocBench ahead of much larger VLMs |
| Checker tier 1 text reasoning | Small local LLM | Current `MODEL_TEXT` / Sarvam | Existing OCR-text path |
| Checker tier 2 vision | Qwen-VL small (2B to 4B, quantized), SmolVLM2 (\~2B) | Current `MODEL_VISION`, Gemini Flash free tier, OpenRouter free vision models | Free tiers may train on requests |
| UI element hints (optional) | OmniParser, UI-TARS, ShowUI | n/a | Only if OCR misses unlabeled icons |
| Transcription | Whisper (local) | Whisper API, Sarvam Saaras v3 | Unchanged from v2 |

Free hosted limits change without notice (Gemini and Mistral no longer publish free-tier limits), so treat them as prototyping tools, not dependencies.

## Worked example: Monthly Actuals mock recording

In the 24-second mock, the speaker clearly states 2 steps while the screen likely shows 8; v3 reaches the silent one (finding the AWS row) that v2 cannot. Screen events are assumed from the transcript until the video is processed.

### Transcript (Stage 0)

| Seg | Time (s) | Speech |
| --- | --- | --- |
| 0 | 1.3 to 3.3 | "So we have our master files here." |
| 1 | 3.3 to 5.8 | "What we do is we go to monthly actuals." |
| 2 | 5.8 to 11.9 | "Based on what value we have, we fill the actualization, taking the data from transactions." |
| 3 | 11.9 to 18.4 | "So Amazon Web Services, I copy it, go to monthly actuals, I paste it here." |
| 4 | 18.4 to 21.8 | "MVA, um, total will get updated automatically." |

### Assumed screen events (Stage 0b)

| Event | Time (s) | Type | What changed |
| --- | --- | --- | --- |
| ev-1 | \~1 | scene\_cut | Workbook on screen |
| ev-2 | \~4 | scene\_cut | Sheet switch to Monthly Actuals |
| ev-3 | \~7 to 9 | scene\_cut | Switch to Transactions sheet |
| ev-4 | \~10 to 12 | large\_shift | Scroll or filter to the AWS row |
| ev-5 | \~13 | local | Copy border around one cell |
| ev-6 | \~15 | scene\_cut | Back to Monthly Actuals |
| ev-7 | \~16 to 17 | local, 2 regions | Pasted cell plus totals recalculating |

### Where each flag comes from (Stages 2 and 2b)

| Event | Matched speech | Bucket | Flag origin | Likely Checker path |
| --- | --- | --- | --- | --- |
| ev-1 | seg 0 "master files here" | vague | reviewer (pointer word) | Tier 1: OCR title bar |
| ev-2 | seg 1 "go to monthly actuals" | explained | none | n/a |
| ev-3 | seg 2 "taking the data from transactions" | vague | reviewer (missing name) or coverage | Tier 1: OCR tab name |
| ev-4 | none (falls inside seg 2, no step covers it) | silent | **screen** | Tier 2: vision, scroll vs filter |
| ev-5 | seg 3 "I copy it" | vague | reviewer (selection rule) | Tier 1: OCR padded crop, row and column header |
| ev-6 | seg 3 "go to monthly actuals" | explained | none | n/a |
| ev-7 | seg 3 "paste it here", seg 4 "updated automatically" | vague + consequence | reviewer (pointer word, unverified consequence) | Tier 1 for target cell; Align confirms a change after paste, formula needs the work file |

### Resulting SOP

1. Open the master workbook. *(said, resolved by title bar OCR)*
2. Go to the Monthly Actuals sheet. *(said)*
3. Go to the Transactions sheet. *(inferred step)*
4. Find the Amazon Web Services row by filter or scroll. *(inferred step from screen-origin flag)*
5. Copy the value from \[column X\]. *(said, clause inferred by OCR)*
6. Return to Monthly Actuals. *(said)*
7. Paste into \[row AWS, month column Y\]. *(said, clause inferred by OCR)*
8. Totals and MVA recalculate. *(said, consequence confirmed by screen change; formula unresolvable without the file)*

### Questions that go back to the recorder

- What is MVA, and what formula produces it?
- Is this repeated for every vendor, and how are vendors matched between sheets?
- Which month column receives the paste, if not legible on screen?

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

## Roadmap

Build in four increments, each with an exit check before the next starts.

| # | Increment | Exit criteria |
| --- | --- | --- |
| 1 | 0b Screen timeline on the mock and 2 real recordings; plot change score against transcript | Every true step visible as an event when checked by eye; false events listed |
| 2 | 2b Align, silent-step flags only | ev-4 type steps appear as screen-origin flags; no duplicate of Reviewer flags |
| 3 | Checker evidence: crops, stitching, OCR tier, vision escalation, expansion loop with needs\_more\_context | Same or better verdicts than v2 on the same recordings, lower cost per flag |
| 4 | Event attachment to Reviewer flags, consequence shortcut | Consequence flags resolved or contradicted without a vision call where the screen is clear |
| Later | Work file diff, live buffer mode, per-tool overlay masks | Defined after increment 4 results |

### Eval set (before increment 3)

- [ ] Hand-label true steps in 3 recordings, including silent ones.
- [ ] Record v2 baseline: flags, verdicts, cost, missed steps.
- [ ] Re-run after each increment and compare.
