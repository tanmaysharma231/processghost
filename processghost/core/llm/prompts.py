"""Prompt templates for the LLM-driven pipeline stages.

All three prompts below are used with OpenAI JSON mode (`response_format={"type": "json_object"}`),
so each spells out the exact JSON shape expected back -- the fields must match the corresponding
Pydantic model(s) in `core/models/tokens.py` so the caller in `core/pipeline.py` can construct the
model directly from the parsed response.

These are scenario-agnostic on purpose: no example process, vendor name, or spreadsheet is baked in
here. See Process_ghost/docs/06-known-gaps.md entry 6 for why that matters -- a prompt that hands
the model the answer isn't testing whether it can find the answer.

TRANSCRIPT_EXTRACTION_SYSTEM and CROSS_CHECK_SYSTEM are each called once per run (not once per
transcript segment / per intent-action pair) -- see Process_ghost's plan history for why: giving
the model the whole transcript, or the whole set of extracted entities/claims plus a compact
summary of observed actions, at once lets it do cross-referencing across the entire recording
(a reference used three times and never resolved; a claim made once and never confirmed) that a
narrower per-fragment call structurally cannot do, at lower total cost than many small calls.
"""

TRANSCRIPT_EXTRACTION_SYSTEM = """You are analyzing the full narration transcript of a \
screen-recorded walkthrough of a business process. You will receive every segment of the \
transcript at once, in order, each with an index and its start/end timestamps.

For EACH segment, extract:

1. The single primary intent expressed -- what the speaker says they are doing or about to do.
2. "entities_referenced": every distinct thing this segment mentions (a file, a sheet, a tab, a \
column, a button, a field, a value, a status, a person) -- for each one, record:
   - "raw_mention": the exact words used to refer to it
   - "entity_role": one of "location_or_container" (a file/sheet/tab/folder/page), "target_field" \
(a column/cell/input/button/menu item), "data_value" (a specific piece of data), \
"condition_or_status" (a state something must be in, e.g. a status flag), "actor" (a person/role), \
or "other"
   - "named_specifically": true only if named precisely enough that someone unfamiliar with the \
process could find it without guessing (e.g. "the Monthly Actuals sheet" is specific; "here", \
"this one", "the usual place", or a bare pronoun with no earlier antecedent is not)
3. "selection_criteria": if this segment explains HOW to pick or find the right item among \
several possible ones (e.g. "by matching the reference number", "the one from this month"), quote \
or paraphrase that explanation. Otherwise null. Do not infer criteria that isn't actually stated.
4. "claimed_outcome": if this segment asserts that something will happen or has happened as a \
result of the action -- especially something described as automatic or as a consequence of \
another step (e.g. "it updates automatically", "that gets saved", "this triggers the approval") \
-- quote or paraphrase that claim. Otherwise null.

Important: you can see the WHOLE transcript at once. Use later segments to help resolve a vague \
earlier reference if the speaker clarifies it eventually (e.g. names the sheet properly later on) \
-- in that case "named_specifically" for the earlier mention may still be false (it wasn't \
specific *at that moment*), but note the clarification exists. Still flag a reference as vague if \
it is NEVER clarified anywhere in the entire transcript.

Respond with a JSON object with exactly one field, "segments", whose value is a JSON array with \
exactly one object per input segment, in the same order, each with these fields:
{
  "segments": [
    {
      "segment_index": <int, matching the input segment's index>,
      "intent": "<short paraphrase of what the speaker intends to do, in your own words>",
      "verb": "<primary action verb, lowercase, or null if none is clear>",
      "obj": "<the object/target of the verb as stated, or null if none is stated>",
      "confidence": <float 0.0-1.0>,
      "is_vague": <true if the segment's primary reference is not named specifically, else false>,
      "entities_referenced": [{"raw_mention": "...", "entity_role": "...", "named_specifically": bool}],
      "selection_criteria": <string or null>,
      "claimed_outcome": <string or null>
    }
  ]
}

If a segment contains no actionable content (small talk, a pause, an aside), set "intent" to an \
empty string, "verb"/"obj"/"selection_criteria"/"claimed_outcome" to null, "entities_referenced" \
to an empty list, "confidence" to a low value, and "is_vague" to false.

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


CROSS_CHECK_SYSTEM = """You are auditing a narrated walkthrough for gaps between what was said and \
what was actually observed happening on screen.

You will receive two compact lists:
1. "narration_items": each with an id, the entities it referenced (with whether each was named \
specifically enough to find), any selection/matching criteria it stated, and any outcome it \
claimed.
2. "observed_actions": each with an id, a timestamp, and a short description of what was actually \
seen on screen (application, focused UI element, details).

For each entity reference that was NOT named specifically: check whether any observed action, \
anywhere in the list, effectively resolves what it refers to (e.g. an action's target names the \
same thing precisely, even if the narration never did). If nothing resolves it, it is unresolved.

For each claimed outcome: check whether any observed action plausibly confirms that outcome \
actually happened (e.g. a claim that "the status changes" is only confirmed by an action that \
shows a status-like field actually being edited -- an unrelated action nearby in time does NOT \
count as confirmation). If nothing confirms it, it is unconfirmed.

Only report genuine gaps. Do not flag an entity or claim that is clearly resolved or confirmed.

Respond with a JSON object with exactly these fields:
{
  "unresolved_entities": [
    {"intent_id": "<id from narration_items>", "raw_mention": "<the exact wording>", \
"reason": "<why nothing resolves it>"}
  ],
  "unconfirmed_claims": [
    {"intent_id": "<id from narration_items>", "claimed_outcome": "<the claim>", \
"reason": "<why nothing confirms it>"}
  ]
}

Either array may be empty. Respond with only the JSON object, no other text."""
