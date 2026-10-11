# 24 - Speech to screen-diff deep dive, and the reconciler it points to

Written 2026-10-11 after scoring real output against `12-real-scenario-target.md` (the six required
findings) and finding that gaps found only where the Reviewer is unsure miss the important ones.
Prompted by the observation that every identified step should raise a "what changed here?" question,
not only the uncertain ones.

Method: one real run (`20261011-074659-2dbb1b`), word-level Whisper timestamps (one extra call,
about $0.002), the 7 screen events from `core/screen/timeline.py`, and local OCR of (a) the
top strip of each after-frame (where the spreadsheet's cell-reference box and formula bar sit) and
(b) the before/after crop of each changed region. One 24-second spreadsheet clip; nothing here is
shown for web or ERP screens.

## What was said, when, against what changed

| Event | Time (s) | Words spoken around it | Cell reference / formula bar after | What changed (local OCR, before -> after) |
|---|---|---|---|---|
| ev-001 | 3.0 | "the master files here" (2.1-3.0) | `A1`, `Bill ID` | The changed region is the sheet-tab strip (`Master Bills \| MonthlyActuals \| Transactions-March`); the cell reference `A1` / `Bill ID` is the first sheet's header cell, so this is the first tab |
| ev-002 | 4.3-5.3 | "go to monthly actions" (4.8-5.3) | `F18` | Whole sheet switches to the second tab; lands on cell F18 |
| ev-003 | 6.3 | "based on what value we have" (6.0-7.8) | `C18`, formula bar `AMZNWEBSVCSIN*AWS53871` | Selection moves to C18, the transaction-string cell |
| ev-004 | 8.3 | "we fill the actualization" (8.5-9.5) | `F18` | Selection moves back to F18 |
| ev-005 | 9.7-10.0 | "taking the data from" (10.1); "transactions" (11.1) | `45000` in the formula bar | Switch to the third tab and a cell holding 45000 is selected (copied by 10.5 s) |
| ev-006 | 16.0-16.3 | "go to monthly actions" (16.0-16.5) | `F18` | Back to the second tab, landing on F18 |
| ev-007 | 18.3 | "I paste it here" (17.4-18.2) | `F18`, `45000` | Cell goes empty -> `45.000`; status-bar `Sum:0` -> `Sum:45,000` |

## What this shows

1. **Time is a good clue, not a rule.** Some words land within a fraction of a second of their
   event ("paste it here" ends at 18.2, the paste is at 18.3; "go to monthly actions" at 16.0-16.5 vs
   the switch at 16.0). Others lag a lot: the copy is visible by 10.5 s, but "Amazon Web Services, I
   copy it" is said at 13.4-15.0, four to five seconds later. A fixed window cannot align these;
   content has to (the value 45000 on screen, the name the speaker says).
2. **Every event carries a machine-readable record of what changed.** The cell-reference box and
   formula bar give the cell address and its content (`C18`, `F18`, `45000`); the tab strip gives the
   sheet names; the changed region gives before and after text; the status bar gives a running sum.
   None of that needs a language model to extract.
3. **Several of the six required findings are readable from those records:**
   - Sheet names (findings 1 to 3): the tab strip reads `Master Bills`, `MonthlyActuals`,
     `Transactions-March` (OCR drops the spaces).
   - Paste column (finding 5): the cell reference is `F18` at arrival and at paste, so the column is F.
   - Row matching (finding 4): not stated anywhere, but the records pose the right question: the
     speaker selects C18 (`AMZNWEBSVCSIN*AWS53871`) in one sheet, then copies a cell holding 45000 from
     another sheet's second row. Nothing shows how row 18 was matched to that row.
4. **The "total will update automatically" claim has no supporting change** (finding 6). After the
   paste at 18.3 s nothing changes anywhere else; the only thing that moved with the value is the
   status-bar `Sum`, which is the sum of the selected cell, not the sheet total. The earlier v3 run's
   claim that a "Total Actualized" cell changed from 0 to 235,000 is contradicted by these records: no
   such change appears in any event.
5. **A step is not only a question when the model is unsure.** "Fill the actualization" ->
   selection moved to `C18` then `F18` is a concrete, checkable change. Recording it for every step
   gives the reader what changed, and gives a reviewer something to compare the narration against.

## The design this points to: a reconciler stage

Replace Align's event-counting heuristic with one batched model call per run (whole context at once,
consistent with how the Writer and Reviewer work).

**In:** the ordered steps with word-timed speech, and every screen event with its machine-extracted
change record (type, time, cell reference / formula bar, tab strip, status bar, changed-region text
before and after, and a pointer to the keyframes).

**Out, for every step:**
- the events that realise it (many-to-many), each with a short "what changed" written only from the
  record text;
- what the narration leaves unspecified about that change (which cell, which row and why, which
  column), as questions;
- whether any consequence the speaker claims ("the total will update") has a matching change.

**Out, for every event:** the step it belongs to, or `silent` if none.

**Checked in code, as the Reviewer's transitions already are:** every "what changed" string must be
a verbatim substring of the record it cites, with the same normalization `quote_is_grounded` uses.
A change the records do not contain (the invented 0 -> 235,000) is rejected, not trusted.

**Flags then come from three sources, not one:** an event with no step (silent step), a step with no
event (said, not shown), and a claim with no matching change (unverified consequence), plus the
per-step "what was left unspecified" questions. The Reviewer's transcript-only flags remain.

## What this does not solve

- The record extraction is spreadsheet-shaped (name box, formula bar, tab strip, status bar). A web
  or ERP page has different chrome (URL, breadcrumb, field labels); the extractor would need a
  per-surface profile, and none has been tested.
- OCR errors are real (`45.000` for `45,000`, dropped spaces). Grounding must tolerate them or it
  rejects true statements.
- Why row 18 was chosen (finding 4, the single most important gap) is still only a question: the
  diffs show positions, not the rule. Answering it needs the workbook (see the work-file idea in
  `22-architecture-v3.md`) or the recorder.
- Two actions inside one second merge into one event (the sheet switch and the cell selection in
  ev-005), so the record for that event holds both.
