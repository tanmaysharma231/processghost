# 23 - Checker learnings (replay of 5 real v2 runs)

Evidence for the v3 design, from `scripts/checker_replay.py` run over the five full runs of
`data/english_hackathon_test.mp4` on 2026-08-30 (`20260830-152149-688fe9`, `-152856-992357`,
`-155412-ab0056`, `-160025-f5664c`, `-160732-e1b16b`). No API calls were made; the replay reads
`sop.json`, `run.json`, `trace.jsonl` and `checker_frames/`.

## What the Checker was sent

- 38 flags across the five runs, up to 16 full frames per call.
- The 16-frame cap subsamples the 0.5 s extraction grid, so the frames sent are about 1 s apart.
  Each frame-to-frame change therefore spans several user actions.

## What changed between those frames (546 consecutive pairs, pixel diff)

| Change size (share of pixels) | Pairs | Share |
|---|---|---|
| Tiny, under 0.1% (cursor, caret, status bar) | 370 | 68% |
| Local, 0.1% to 5% (cell selection, a value appearing) | 106 | 19% |
| Large, over 5% (sheet switch) | 70 | 13% |

- Two thirds of what the Checker paid to look at carried no meaningful change.
- A spreadsheet sheet switch changed only about 10.8% of pixels (most cells are white and stay
  white). A rule like "over 40% changed means a new scene" never fires on this recording.
- Sheet switches show up as one large box over the grid plus boxes on the tab strip and status bar.
  Edited or selected cells show up as small boxes.
- A crop of the changed box does not say which sheet or file it came from. The sheet name sits in
  the tab strip, so evidence needs a full downscaled frame and OCR text of the chrome as well as
  crops.

## What the Checker said

- Echo score = share of the answer's content words already present in the prompt (question, step
  text, transcript excerpt). High means the "observation" came from the prompt, not the screen.
- Mean echo: 0.34 for resolved, 0.54 for unresolvable. One unresolvable flag scored 1.0: its
  `observed` field was the step title, instruction and transcript restated word for word.
- Runs 1 and 2 have no echo score because their traces predate payload logging.
- 15 of 38 flags ended `check_failed`, all from OpenAI rate limits (see docs/18 entry 4).

## What this means for v3

1. Change-driven evidence is justified: most sampled frames added nothing, and diffing finds the
   few that did.
2. Evidence must include full-frame context (downscaled after-frame plus OCR text of the title,
   tab and status strips) as well as crops of the changed boxes.
3. Scene/context-switch detection cannot use changed-pixel fraction alone. Use chrome-text change,
   large-box coverage and a structural-similarity score, plus a shift test to tell scroll from
   navigation. Untested on web/ERP recordings: the only video available is a spreadsheet.
4. The Checker prompt needs a guard against echoing: require `observed` to cite something not in
   the prompt, or score it and treat high echo as unresolvable. Not done yet.
5. One failed call costs a flag's worth of evidence, so evidence assembly should be cheap and
   cached, and retries should only cover transient errors.

## Caveats

- One 24-second spreadsheet recording; the numbers describe this clip, not screens in general.
- The echo score is a lexical proxy. A high score does not prove the answer was wrong, and a low
  score does not prove it was right.

## First real v3 run (2026-10-11, run `20261011-074006-ce3086`)

Same clip, `python -m scripts.run_pipeline ../data/english_hackathon_test.mp4 --screen`, OpenAI path:
screen timeline (7 events) -> Align -> event-evidence Checker.

| | v2 (five runs, 2026-08-30) | v3 (one run) |
|---|---|---|
| Whole-run cost | about $0.24 to $0.38 | $0.137 |
| Flags that ended `check_failed` | 15 of 38 (rate limits) | 0 of 8 |
| Checker cost per flag | about $0.04 to $0.06 | about $0.015 (rough: run total minus Writer/Reviewer, over 8 flags) |
| Flags resolved at level 0 (OCR text only, no images) | not applicable | 5 of 8 |
| Flags needing the level-1 vision escalation | not applicable | 3 of 8 |

Caveats: one run on one clip; the Writer produced 9 steps this time (the earlier runs had 5-6), so the
flag sets are not identical; costs are token-estimated.

What the observations look like now: they quote strings that are actually on screen
("AMZNWEBSVCSIN*AWS53871", "PageStyle_MonthlyActuals", "Sum: 45,000", "Total Actualized"), which the v2
observations often did not. The echo guard did not fire in this run.

What is still wrong, found by reading the answers:
- **Resolved is too easy.** Several "resolved" answers are generic: "transaction data includes
  transaction string and category", "the relevant cells under the headers ...", "verify the displayed
  sum matches expected total". They name nothing the question asked for (which column, which cell). The
  verdict rules accept any added noun. Nothing in the repo measures this yet; it needs labelled
  expected answers per flag.
- **Silent-step detection found nothing here.** With 8 Reviewer/coverage flags on a 24 s clip, every
  screen event sits inside some flag's window, so Align raised no `origin: screen` flag. The feature
  is unproven on any data. Attachment initially used a +/-3 s lead on both sides and attached almost
  every event to some flag; it now allows a lead before the window only. Even so, every event
  still counts as near a flag ("vague").
- **Align's buckets do not discriminate yet** for the same reason, and the events-per-step allowance
  (`SCREEN_ALIGN_MAX_EVENTS_PER_STEP`) is untuned.
