# ADR 0014: One flow from what was said and what was shown, four questions per data step, and an evidence ladder

**Status:** Accepted, built; unit-tested on synthetic data and the screen side dry-run on one real clip; the model-driven parts (slot questions, Reconciler actions and slot fills, the document loop) have not been run against a real model

## Context

The goal is a process flow with no missing steps. Four things stood in the way (checked in the code):

1. Screen findings never became steps. An unnarrated change became a flag asking what happened; observed changes were notes on spoken steps. The flow was the narration with annotations.
2. Gap finding depended on the Reviewer noticing vague wording. How rows were matched was never asked in four real runs, and no video or document can answer a question that is not raised.
3. Pixel change size says nothing about importance: a dropdown can change a third of the screen and mean nothing, a one-cell edit a fraction of a percent and be the process.
4. A question the video could not answer simply stayed open; the workbook the person worked in was never read, although rules (a formula, a column's contents) live there and the screen shows only values.

## Decision

- **Steps come from both sources.** An event nobody narrated that is a navigation or a data change, and whose action sentence is backed by a verbatim quote from that event's record, becomes a step marked "done on screen, not said" (`Attribution.kind = "shown"`), placed by time. Anything else stays a silent-change flag.
- **Events are classified by what changed in the application** (`core/screen/classify.py`), never by pixel size: transient, view-only, navigation, data change, selection, unclear. Only positive proof drops an event; every dropped event is listed with its reason; unclear is always kept. When the selected cell moves, text that appears or vanishes because the cell's own content is shown or hidden is a selection, not an edit.
- **A selection is an anchor** (`core/screen/anchors.py`), tied to speech (a pointer word or the cell's value spoken near it) and to a later data change that puts the same value elsewhere. Two independent signals confirm it; one makes it a candidate worded "possibly"; ambiguity yields candidates, never a guess. A confirmed anchor answers a copied value's source and target with no model.
- **Every step that moves or edits data must answer four questions** (`core/slots.py`): source, key, target, effect. A missing answer becomes a question by omission, like a missing transition. A slot counts as said only with a verbatim transcript quote.
- **Every question climbs a ladder: video, then document, then the person who recorded the process.** The video rung is the Reconciler, the anchors and the Checker. The document rung (`core/data/`) is a deterministic profiler, two read-only lookup tools and a short bounded loop of structured calls; every citation must match a cell a lookup actually returned, and the value and formula are copied from the lookup result, not from the model. The person is last for answering and final for intent: a conflict between sources (the formula says one thing, the stored values another) is never settled by the pipeline; it reaches the person as a conflict, and a question the data only narrowed to candidates reaches them as a confirmation.
- **The workbook is checked against the recording first** (`core/data/match.py`): the sheet count, the content of selected cells and the visible words. A mismatch stops the workbook being used and the report says so; a partial match is used with a warning. The first real run showed why: the supplied file had one total row where the recording had two.
- **Answers that code can compute are computed** (`core/data/answer.py`, `facts.py`): which cell was selected (the cell trail, read from the name box), which formulas read it with their literal criteria checked against the real cells, and which columns carry the same value on two sheets. The model loop only takes what is left.
- **A number in a video claim must belong to a cell that depends on what the recording changed** (`core/data/guard.py`). The Checker read the billed total and attached it to the actualized total one row below; grounding the number in the evidence could not catch that. Totals are computed from the cells because the file stores no results.
- **"One shown, many needed" is found from the workbook** (`core/data/repeat.py`): the range a formula reads that holds the changed cell (or, with no formula, the run of cells waiting for the same entry) says how many items the process covers; the recording changed one. It is raised as a question, not added as a step.
- **The flow is drawn with its gaps** (`report/flow.py`): step nodes marked said / shown / both, dashed gap nodes where a piece is missing, each linked to its question. A flow with no gap nodes says "no known gaps", never "complete".
- **Reading failures are retried with a changed input, then read once by a cheap vision model on the failed crop only** (`core/screen/read_fallback.py`), behind the same validator as OCR; over the per-run cap a field stays unread.

## Consequences

- The data pass writes next to the base, never over it: base text is not edited, answers are attached as resolutions, slots and rungs. A cross-check can turn a resolved question into a conflict; that is intended.
- Cell text and a few cropped screen strips go to the model provider. `docs/data-handling.md` says so.
- Reconciler questions are no longer terminal: they can be resolved by the document rung.
- Measured on the one clip with a real model (2026-10-22): the second run scored 9 of 10 findings at $0.31 (the first, before the facts, guard and match check, scored 3 of 6 at $0.54); the remaining finding (repeat for every other transaction) is what the repeat detection added afterwards, replayed on the saved run but not yet re-run with a model. The owner confirmed that setting Status to "Paid" is not in the recording, so the pipeline raising it as a question is the intended result.
- What is NOT claimed: that the flow is complete (an action off screen, or a rule the data does not hold, never appears), that the event classes generalise beyond the one clip, or that the model parts work; those need paid runs and human-confirmed labels (`expected_classes.json`, `expected_steps.json`).

## Rejected alternatives

A generic "needs data" tag on flags (it would be set on nearly everything); a pixel or area threshold for importance; dropping selections or turning each into a step; letting the data pass overwrite the video's answer; native model tool calling for the lookup loop (it would bypass the shared retry, cache and cost layer; the structured loop is bounded and replayable instead); freeze-and-replay machinery for the comparison (A, B and C are read off one run's per-question rung record).
