# 01 - Product Overview (as-is)

## The problem: expertise blindness

An expert who has done a task hundreds of times explains it in twenty seconds and skips almost
everything a first-time user needs, because the missing pieces are invisible to the expert -- they
stopped noticing them long ago. ProcessGhost's premise is to turn a screen-recorded walkthrough plus
its audio narration into a written SOP that recovers what the expert *did* but did not *say*, by
running the walkthrough through a pipeline of agents that each catch a different category of
omission (unstated prerequisites, vague references, unnamed UI targets, unverbalized clicks).

This framing is not something written up anywhere as prose in the repo -- it has to be reconstructed
from a hardcoded scenario buried in `app/page.tsx` and `app/demoData.ts`. That scenario is the
clearest existing statement of intent in the whole codebase, so it is reproduced here in full as the
product's working example.

## The worked example baked into the repo

Source: `DEMO_CONTEXT` (`app/page.tsx:65-77`) and `DEMO_TRANSCRIPT_TEXT`
(`app/demoData.ts:9`).

**Setup.** A company bills tracker, `ProcessGhost_Bills_Tracker.xlsx`, with three sheets:
- **Master Bills** -- reference list, Bill IDs mapped to clean vendor names.
- **Transactions - March** -- raw, garbled bank transaction strings, each carrying a Bill ID and an
  actualized amount.
- **Monthly Actuals** -- where actual amounts get entered, month by month, with a `SUMIF` formula
  at the bottom of each month's section that totals rows where Status = `"Paid"`.

**The walkthrough** (`english_hackathon_test.mp4`, ~20 seconds, one speaker) is transcribed
verbatim as:

> "We have a master files here. What we do is we go to monthly actions. Based on what value we
> have, we fill the actionization, taking the data from transactions. Amazon Web Services, I copy
> it, go to monthly actions, I paste it here, and the total will get updated automatically."

**What the expert skipped**, enumerated explicitly in `DEMO_CONTEXT.expertSkips`
(`page.tsx:68-76`):
1. "master files" -- never specifies this is a separate sheet called "Master Bills".
2. "monthly actions" -- the actual sheet is "Monthly Actuals", mispronounced.
3. Matching logic never explained -- how to identify which row corresponds to which transaction
   (by Bill ID).
4. "transactions" -- never says this is a sheet called "Transactions - March".
5. "Amazon Web Services" -- but the actual data in the sheet is garbled:
   `AMZN WEB SVCS IN *AWS53871`.
6. "I paste it here" -- no column specified (it is Column F, "Actual").
7. "total will get updated automatically" -- never mentions that Status must read exactly `"Paid"`
   for the SUMIF formula to count the row, and never warns against overwriting the formula row.

This is the product in miniature: seven sentences of spoken instruction, seven distinct pieces of
tacit knowledge a new hire would be missing, each invisible to the expert and each capable of
causing a wrong or broken entry if followed literally.

## What the pipeline is supposed to add

Given the transcript above, the six-agent pipeline (full detail in `03-agent-pipeline.md`) is
designed to:
1. Transcribe the audio with timestamps and speaker labels.
2. Extract only what was explicitly said, as a numbered step list with evidence quotes.
3. Detect the gaps between what was said and what a newcomer would need -- the seven items above.
4. Recover, from the video itself, what the expert did but never described (which sheet tab was
   actually clicked, which column the value actually landed in, what the formula bar actually
   showed).
5. Synthesize a clean SOP where every step traces back to either the transcript or a recovered
   visual/gap annotation, with inline explanations of *why* each addition matters.
6. Have a "senior process auditor" agent judge the draft for completeness and route it back for
   another pass if gaps remain, or approve it.

## Two important caveats about the current build

These are stated as facts here because they define what "the product" currently actually is,
versus what it is designed to be; each is expanded with full evidence in `06-known-gaps.md`.

- **Step 4 (visual recovery) is not implemented against real video.** No frame of the actual
  screen recording is ever extracted or sent to any agent. See `06-known-gaps.md` entry 1 and
  `09-visual-gap-filler.md` for the target-state design that would make this real.
- **The demo that showcases this exact scenario makes no agent calls at all.** The worked example
  above is real and well-constructed, but clicking "Try with Sample Data" and "Generate SOP" simply
  plays back hardcoded output (`app/demoData.ts`) with animated delays. It demonstrates the
  intended *result*, not the pipeline actually producing it. See `06-known-gaps.md` entry 2.

## Output format

The end product is a structured SOP: numbered sections, each containing numbered steps. Every step
carries an `annotation_type` (plain instruction vs. recovered verbal gap vs. recovered visual
action vs. judge-flagged), the `evidence_quote` it traces back to, a `bridging_explanation` of what
was missing and why it matters, and a `source` tag. A parallel `gap_report[]` lists every detected
gap and how it was resolved. The whole thing exports to Markdown or downloads as a generated file
(when the synthesis agent's `module_outputs.artifact_files` is populated). Full schema in
`04-data-contracts.md`; full rendering behavior in `05-frontend-behavior.md`.
