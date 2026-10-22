# ADR 0013: Surface locators are the context map; speech is corroboration, never a gate

**Status:** Accepted; the spreadsheet profile matched the draft labels on one clip, the web profile is unproven

## Context

Whether the application, sheet or page changed cannot be read from how many pixels moved: a spreadsheet sheet switch changed about 11% of pixels. Which sheet or page is showing is written in the frame's chrome (cell reference and formula bar, status bar, tab strip, URL, breadcrumb). Narration lagged the action by 4 to 5 seconds on the measured clip and some navigation was never narrated at all.

## Decision

A small profile per kind of surface reads the chrome text with no model and returns a `Locator` (application, document, location, title, focus, formula-bar content, the evidence strings it came from, a confidence). Profiles exist for spreadsheets and generic web pages, with a low-confidence generic fallback. Comparing the before and after locators of an event says whether it was an application, document, sheet or page change, or an edit on the same surface; when a profile is not confident the answer is "unknown", never a guess. A sheet is identified by its position ("Sheet 2 of 3") first because the name is easier for OCR to misread.

The locators build a `ContextMap`: each distinct context is described once (its locator and capped OCR of the first full frame seen there) and reused when the recording returns to it. It stores observations only. A relationship such as "this column is the matching key" is a hypothesis and is not stored.

Speech is a cross-check (`core/screen/speech_clue.py`): did the speaker name the place the event moved to, looking mostly forward in time because narration lags? A missing clue on a real switch is a signal (possible silent navigation), not a verdict, and a present clue never confirms the event on its own.

## Consequences

- On the one real clip the dry run recovers all three sheets, reuses the second when the recording returns to it, and matched every scored field of the draft labels. Those labels are not independent of the detector's own readings, so this is a regression check, not proof.
- The OCR often garbles or drops the formula-bar glyph; the profile falls back to a lone cell reference and then claims no formula content rather than guess it. The third sheet showed no readable cell reference.
- The web profile has only synthetic tests. A web page change is not a large pixel change, which is why locators, not pixels, decide it.
- A hosted vision model for unrecognised surfaces was considered and is deferred: it would send screen images to a provider and cannot be validated without credit and a web recording.

## Rejected alternatives

Using the changed-pixel fraction to decide a context change (it fails on sheet switches). Using speech as a gate for treating an event as navigation (it lags and is sometimes absent). A vision model on every context switch (cost and data handling, for a question usually answerable from text). Named profiles for specific ERP products before any recording of one exists.
