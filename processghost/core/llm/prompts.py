"""Prompt templates for the LLM-driven pipeline stages.

Both prompts below are used with OpenAI JSON mode (`response_format={"type": "json_object"}`), so
each spells out the exact JSON shape expected back -- the fields must match the corresponding
Pydantic model in `core/models/tokens.py` (`IntentToken`, `ActionToken`) so the caller in
`core/pipeline.py` can construct the model directly from the parsed response.

These are scenario-agnostic on purpose: no example process, vendor name, or spreadsheet is baked in
here. See Process_ghost/docs/06-known-gaps.md entry 6 for why that matters -- a prompt that hands
the model the answer isn't testing whether it can find the answer.
"""

INTENT_EXTRACTION_SYSTEM = """You are analyzing one segment of narration from a screen-recorded \
walkthrough of a business process. The speaker is explaining what they are doing as they do it.

Extract the single primary intent expressed in this segment -- what the speaker says they are \
doing or about to do. Also flag whether the phrasing is vague: does it refer to something (a \
file, a sheet, a button, a value, a location) without naming it specifically enough that someone \
unfamiliar with the process could find it? Phrases like "here", "this one", "the usual place", or \
a generic verb with no named target ("check it", "update it") are vague. A specific, named \
reference ("the Monthly Actuals sheet", "column F") is not vague.

Respond with a JSON object with exactly these fields:
{
  "intent": "<a short paraphrase of what the speaker intends to do, in your own words>",
  "verb": "<the primary action verb, lowercase, e.g. 'open', 'copy', 'paste', 'filter', 'export', \
or null if none is clear>",
  "obj": "<the object/target of the verb as stated, e.g. 'the master sheet', or null if none is \
stated>",
  "confidence": <float 0.0-1.0, how confident you are that you extracted the correct intent from \
this segment>,
  "is_vague": <true or false, per the definition above>
}

If the segment contains no actionable intent (e.g. small talk, a pause, an aside), set "intent" to \
an empty string, "verb" and "obj" to null, "confidence" to a low value, and "is_vague" to false.

Respond with only the JSON object, no other text."""


ACTION_DESCRIPTION_SYSTEM = """You are looking at a single frame captured from a screen recording \
of someone performing a business process (e.g. working in a spreadsheet, a web app, or other \
software). You do not have audio or surrounding frames -- describe only what this one frame shows.

Identify what on-screen state or action this frame captures: what application or view is visible, \
what specific UI element (if any) appears to be the focus (a cell, a button, a menu, a tab, a \
field), and what the frame suggests the user just did or is about to do.

Respond with a JSON object with exactly these fields:
{
  "action_type": "<a short snake_case label for the action/state, e.g. 'open_file', \
'switch_tab', 'edit_value', 'filter', 'sort', 'export', 'submit', 'approve', 'login', \
'view', or 'unknown' if nothing identifiable is happening>",
  "target": "<the specific UI element or location in focus, e.g. 'Monthly Actuals tab', \
'Column F row 12', or null if not identifiable>",
  "details": "<one sentence describing what is visible and what it suggests the user did, in your \
own words>",
  "app": "<the application or product visible, e.g. 'Excel', 'Google Sheets', 'a web browser', or \
null if not identifiable>",
  "confidence": <float 0.0-1.0, how confident you are in this description given only one frame>
}

If the frame is blank, transitional (e.g. mid-animation), or otherwise uninformative, set \
"action_type" to "unknown", "target" and "app" to null, "details" to a brief note saying why, and \
"confidence" to a low value.

Respond with only the JSON object, no other text."""


ALIGNMENT_JUDGE_SYSTEM = """Given an intent (what the speaker said they were doing) and an action \
(what was observed on screen), judge how compatible they are as a description of the same real-world \
step.

Respond with a JSON object with exactly these fields:
{
  "score": <float 0.0-1.0, where 1.0 means the action is clearly what the intent describes>,
  "reason": "<one short sentence explaining the score>"
}

Respond with only the JSON object, no other text."""
