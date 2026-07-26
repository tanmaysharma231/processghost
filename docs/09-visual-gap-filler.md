# 09 - Visual Gap Filler, Designed (Python rebuild)

This is the fix for `06-known-gaps.md` entry 1 -- the highest-severity gap and the product's
central unimplemented claim. Target-state design only.

## Why this is the priority

The whole premise of ProcessGhost (`01-product-overview.md`) is recovering what an expert *did* but
did not *say*. Steps 1-3 and 5-6 of the pipeline (transcribe, extract, detect gaps, synthesize,
judge) all operate purely on text derived from the audio track. Only step 4 has any claim to
observing the screen itself, and as documented in `03-agent-pipeline.md` and `06-known-gaps.md`
entry 1, it currently does not -- it reasons over a paragraph the developer wrote describing the
spreadsheet from memory. Without this fix, "visual gap filler" is a misnomer for a second
text-reasoning pass with no additional information.

## Pipeline

```
GapList (from stage 3)              source video file
    |  each gap has                       |
    |  timestamp_start/timestamp_end       |
    v                                      v
        frame extraction (ffmpeg) at each gap's timestamp window
                    |
                    v
        optional crop (ffmpeg crop filter, real pixels this time)
                    |
                    v
        frame selection / downscale / encode
                    |
                    v
        vision model call: images + gap context -> VisualFill per gap
                    |
                    v
        VisualFillList (stage 4 output, feeds stage 5)
```

`GapList` already carries `timestamp_start`/`timestamp_end` per gap
(`response_schemas/gap_detection_agent_response.json`, `04-data-contracts.md` section 1.3) -- this
data exists today and is simply unused for anything visual. It is the natural join key between
"what was said" and "what to look at."

## Frame sampling strategy

Three options considered:

- **Fixed count per window** (e.g. always extract 3 frames, evenly spaced). Simple, predictable
  cost, but wastes frames on static screens and may miss the one frame that shows a click/menu
  state on a window with more visual activity.
- **Uniform interval** (e.g. one frame every 0.5s across the window). Better coverage on active
  windows, still wastes frames on static ones, and scales cost with window length rather than
  actual content.
- **Scene-change detection** (`ffmpeg -vf "select='gt(scene,THRESHOLD)'"` within the timestamp
  window, with a floor of at least 1 frame and a ceiling of N frames). Matches the actual content of
  a screen recording: screen recordings are mostly static (someone reading, thinking, talking) with
  discrete jumps when a click, menu, or navigation actually happens -- exactly the moments this
  stage needs to catch.

**Recommendation: scene-change detection**, with a floor of 1 frame (guarantee at least the first
frame of the window even on a fully static gap) and a ceiling of ~5 frames per gap window (cap
payload size and cost). Fall back to a uniform-interval sample within the window if scene-change
detection returns zero frames above threshold, so a window is never sent with no image at all.

## Crop

Make the existing `x/y/w/h` percentage controls (`UploadSection.tsx:145-161`,
`06-known-gaps.md` entry 9) operate on the extracted frames via ffmpeg's `crop` filter, computed
from the percentages against the actual video's resolution, before the frame is sent to the vision
model. This is what makes the crop control's stated purpose -- "focus the vision agent on a
specific screen region" -- actually do something. If no crop is set, send the full frame (matching
current default behavior).

## Cost and payload budgeting

Images are the most expensive input type for a vision model call. Controls:
- Cap frames per gap (ceiling above) and cap total frames per run (e.g. across all gaps in one
  pass) to bound worst-case cost on a walkthrough with many detected gaps.
- Downscale extracted frames to a fixed max dimension (e.g. 1024px on the long edge) before
  encoding -- screen text needs to stay legible but a 4K screen recording frame is far more
  resolution than any current vision API needs or bills efficiently for.
- Record the **actual** number of frames sent per gap into `VisualFill.frames_analyzed` -- this
  replaces the as-is behavior where the model invents this number with no frames having been sent
  at all (`06-known-gaps.md` entry 1).
- If a gap's timestamp window falls outside the video's actual duration (bad data from stage 1 or
  3), that gap is marked visually-unrecoverable rather than causing the whole stage to fail (see
  `08-target-pipeline-spec.md`, stage 4 failure mode).

## New infrastructure requirements this introduces

Which the as-is app has none of, since it never touches raw video pixels at all:
- **ffmpeg on PATH** in whatever environment runs this stage (local dev, CI, deployment target).
- **Local temporary storage** for extracted frame images between extraction and the vision model
  call, plus a **cleanup policy** (delete after the stage completes, or after the run completes,
  bounded by a max retention so a server doesn't accumulate frame dumps indefinitely).
- **A vision-capable model** for this stage specifically -- carried over from `workflow_state.json`
  (Gemini 2.5 Pro is already vision-capable, so the existing model choice for this agent role does
  not need to change, only what is actually sent to it).

## What stays the same

The output contract (`VisualFillList` / `visual_gap_filler_agent_response.json`) does not need to
change shape -- `gap_id`, `related_step`, `screen_actions`, `ui_elements_identified`,
`navigation_path`, `data_entered`, `visual_step_description`, `confidence` all remain meaningful
fields for a model that is now actually looking at frames instead of prose. Only `frames_analyzed`
changes in meaning, from invented to accurate. See `10-migration-map.md` for the full field mapping
into the target pydantic model.
