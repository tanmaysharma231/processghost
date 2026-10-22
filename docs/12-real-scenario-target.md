# 12 - Real Scenario: Steps Shown, Steps Missing, and the Target Output

> **Correction (owner, 2026-10-22):** the matching key is the **Transaction String**, not Bill ID. A transaction string is unique because it carries a specific name and number; Bill ID is generated per bill for individual products, so it has duplicates (the same id recurs for different use cases, and across months in Monthly Actuals). Read every 'Bill ID' below as 'Transaction String' where it names the join key. The evaluation files in `processghost/evals/cases/bills_tracker/` carry the corrected key.

> **Second correction (owner, 2026-10-22):** setting Status to "Paid" is **not** in the recording. It is a rule of the workbook (the total only counts rows whose Status is "Paid"), so the pipeline should raise it as a question, not state it as a step of the process. Also, the workbook open in the video is not identical to `ProcessGhost_Bills_Tracker_v4_final.xlsx`: the video has a **Total billed** row (27) and a **Total Actualized** row (28); the xlsx has one total row. The pipeline now checks that a supplied workbook matches the recording (`core/data/match.py`).

This is the concrete, grounded goal for ProcessGhost's output, built from the real test files added
at `Process_ghost/data/` (`english_hackathon_test.mp4`, `ProcessGhost_Bills_Tracker_v4_final.xlsx`)
and verified against an actual pipeline run (`processghost/runs/20260725-202601-4491aa/`), not the
abstract placeholder scenario in `01-product-overview.md`. This is the acceptance target for the
SOP-synthesis stage (`08-target-pipeline-spec.md`) once it's built: given this input, the pipeline's
output should look like section 4 below.

## The real input

**Workbook** (`ProcessGhost_Bills_Tracker_v4_final.xlsx`), three tabs in this order:
1. **Master Bills** -- 8 rows, columns: Bill ID, Vendor Name, Category, Expected Amount, Due Date,
   Notes. Reference list of recurring vendors.
2. **Monthly Actuals** -- columns: Month, Bill ID, Transaction String, Category, Expected (E),
   **Actual (F)**, Variance (G, formula `=IF(F<>"",F-E,"")`), **Status (H)**, Entered By, Remarks.
   Jan and Feb 2025 rows are fully filled in. All 8 March 2025 rows (18-25) have **no Actual value
   and no Status** -- they are the work to be done. Row 27 totals March with
   `E27=SUMIF(A18:A25,"Mar 2025",E18:E25)` and, critically,
   `F27=SUMIFS(F18:F25,A18:A25,"Mar 2025",H18:H25,"Paid")` -- **the total only counts a row if its
   Status is exactly "Paid".**
3. **Transactions - March** -- 8 rows, columns: Bill ID, Transaction String, Actualized Amount.
   This is the source data to copy into Monthly Actuals. Transaction strings are garbled bank text
   (e.g. `AMZN WEB SVCS IN *AWS53871` for the AWS bill), not the clean vendor names from Master
   Bills.

**Video** (`english_hackathon_test.mp4`), 23.9s, 1920x1080 with audio. Real Whisper transcript,
verbatim:

> "So we have a master files here, what we do is we go to monthly actions, based on what value we
> have, we fill the actualization, taking the data from transactions. So Amazon Web Services, I
> copy it, go to monthly actions, I paste it here, and the total will get updated automatically."

## 1. Steps shown (explicit, from narration)

What the speaker actually says, in order:

1. References "master files" -- a reference sheet exists.
2. Goes to "monthly actions" -- navigates to a sheet to do the work in.
3. Determines "what value we have" -- some value needs to be identified.
4. Says the value comes "from transactions" -- there's a third sheet with source data.
5. Names "Amazon Web Services" and copies its value.
6. Goes back to "monthly actions" and pastes the value.
7. States the total "will get updated automatically."

## 2. Steps missing (the gaps -- what's never said)

Each of these is real and necessary, confirmed against the actual workbook structure above; none
of them are stated in the transcript:

1. **Which exact sheet is "master files"?** Never named. It's specifically **Master Bills**.
2. **Which exact sheet is "monthly actions"?** Never named correctly (consistently mispronounced,
   twice). It's specifically **Monthly Actuals**.
3. **Which exact sheet is "transactions"?** Never named. It's specifically
   **Transactions - March**.
4. **How is the right row found?** Never explained. This is the single most important missing
   step: the transaction string in Transactions - March for AWS is `AMZN WEB SVCS IN *AWS53871`,
   not "Amazon Web Services" -- matching by the name spoken would fail. **The actual matching key
   is Bill ID** (`BILL-001`), which is present in both Master Bills and Transactions - March but
   never mentioned as the join key. This is exactly the gap the user is describing: "I don't say I
   match the name."
5. **Which column receives the pasted value?** Never named. It's specifically **Column F
   ("Actual")**. "I paste it here" gives no column identity at all.
6. **What makes the total actually update?** The transcript claims it happens "automatically," but
   the real formula (`F27`) only sums a row when **Status = "Paid" (Column H)**. Status is never
   mentioned as something to set. This is worth flagging as a possible real defect, not just a
   narration gap: **the real pipeline run's vision analysis of the 12 sampled frames found repeated
   edits to Column F (Actual) but never observed a Status-column edit** -- meaning either the
   speaker forgot to actually set Status to "Paid" on camera, or it happened in a frame outside the
   sampling window. Either way, "the total will get updated automatically" is not reliably true
   without this step, and that's a finding worth surfacing, not silently assuming.
7. **The Variance column (G) is a live formula and must not be manually overwritten.** Never
   mentioned; not directly at risk in this recording, but a standing hazard in this workbook worth
   carrying as a standing warning (per the same pattern documented for the placeholder scenario in
   `01-product-overview.md`).

## 3. What the fix should say (the target bridging explanation per gap)

This is the actual content the SOP-synthesis stage should produce once built -- not vague "this
was unclear" notes, but the specific missing fact plus why it matters:

| Gap | What the output must state |
|---|---|
| Sheet identity x3 | Name the three sheets explicitly by their real names (Master Bills, Monthly Actuals, Transactions - March) the first time each is referred to, even though the narration never does. |
| Row-matching logic | State plainly: match rows by **Bill ID**, not by vendor name -- the transaction string will not match the spoken vendor name because it's garbled bank text. Show the specific example (AWS: `BILL-001` -> `AMZN WEB SVCS IN *AWS53871`). |
| Paste target column | State the exact destination: **Column F ("Actual")** in Monthly Actuals. |
| Status requirement | State explicitly: Status (Column H) must be set to exactly **"Paid"** or the March total (`F27`) will not include this row, regardless of what's pasted into Actual. Flag this as unverified/at-risk for this specific recording given no Status edit was observed. |
| Formula protection | Warn: do not manually edit the Variance column (G) -- it is a live formula (`=IF(F<>"",F-E,"")`). |

## 4. Definition of done (acceptance target for the SOP-synthesis stage)

Given this exact video + workbook as input, the pipeline's synthesized output is considered correct
when it contains, at minimum, one annotated step or gap entry per row of the table in section 3 --
i.e. it must independently surface all three unnamed sheets, the Bill-ID matching requirement, the
Column F destination, the Status="Paid" requirement (ideally flagged as unverified given the video
evidence), and the Variance-formula warning. Missing any one of these six is an incomplete result,
regardless of how well-written the rest of the document is.

This supersedes `01-product-overview.md`'s worked example as the concrete test case going forward
-- that document used a similar but not identical placeholder scenario (different vendor list,
slightly different numbers) invented before real files existed. This document is grounded in the
actual files now checked into `Process_ghost/data/` and an actual verified pipeline run.
