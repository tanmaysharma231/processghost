# Scope and limits

What ProcessGhost does not do, and which of those limits are deliberate choices versus gaps.

## Deliberate scope choices

- **Recording only.** No system access, no click or keystroke logs. The target source is a meeting
  bot recording a screen share, which will not come with input events. Cost: logic that is neither
  spoken nor visible (a long SQL query behind a button, a formula in a cell nobody selects) cannot be
  recovered. Flags point at it; they cannot resolve it.
- **Single speaker, short recordings.** One narrator, tens of seconds to a few minutes. Long calls
  (20 to 30 minutes) and multi-speaker recordings are untested and out of scope for now.
- **Sequential Checker.** Flags are checked one at a time, which is slow but avoids rate-limit
  storms. There is a run cap (`MAX_CHECKED_FLAGS`, 20) that has never once been reached; the rest
  would be marked `unchecked`, not dropped.
- **Local screen processing.** The screen timeline and OCR run locally (OpenCV, RapidOCR), so
  recordings of client screens need not leave the machine for that stage. The Writer, Reviewer and
  Checker still send text (and, on one escalation path, images) to the chosen model provider.
- **Opt-in v3 path.** `use_screen` is off by default so the v2 pipeline stays byte-identical.

## Known gaps (not choices)

- **The workbook must be the one in the recording.** A different version silently gives answers about the wrong file, so the pipeline compares the sheet count, selected-cell contents and visible words, refuses a clear mismatch and warns on a partial one. The comparison is only as good as the OCR (it warns on garbled words too).
- **A formula's computed value is not in the file** when it was never calculated by a spreadsheet application; totals are computed from the cells for a few functions only (SUM, SUMIF, SUMIFS, COUNTIF, COUNTIFS).
- **"One shown, many needed" needs a workbook.** From the screen alone the extent cannot be seen.
- **Pointer words are English only**, and event classes and anchors were tuned on one clip with draft labels.

- **Evidence base is one clip.** A 24-second spreadsheet walkthrough. Every threshold and every
  number in the docs describes that clip.
- **Web and ERP screens are untested.** Context-switch detection is designed for page-level change
  (chrome-text change, changed-region coverage, a scroll test) but has only ever seen a spreadsheet.
- **Reviewer variance.** The total flag count was fairly stable (7 to 9 across ten runs on a fixed
  draft) but the split between structural and ambiguity flags moved, and the number of grounded
  transitions ranged 0 to 2. What "stable" should mean has not been decided.
- **Weak "resolved" answers.** The Checker sometimes resolves with a generic statement that names
  nothing the question asked for. The echo guard catches restating the prompt, not vagueness.
- **Silent-step detection is unproven.** On the one clip the Reviewer's flags already covered every
  screen event, so Align raised no screen-origin flag.
- **Two quick actions merge.** Actions within the 1-second settle window become one event
  (switching sheets and copying a cell, on the real clip).
- **Eval labels are drafts.** They were read off the detector's own keyframes, so a perfect score
  against them proves nothing about recall. A person has to watch the video and confirm them.
- **Cost figures are token estimates** from a price table sourced on 2026-08-29.

## What would change these

| Limit | What it takes |
|---|---|
| One clip | More recordings with labelled ground truth, including at least one web/ERP walkthrough |
| Reviewer variance | A decision on the stability target, then a measured fix |
| Weak resolved answers | Labelled expected answers per flag and a stricter specificity rule |
| Silent steps unproven | A recording where a step is performed but never mentioned |
