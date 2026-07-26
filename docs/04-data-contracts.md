# 04 - Data Contracts (as-is)

Every declared response schema, and the four-layer normalization stack real data passes through
before it reaches React state. As-is only.

## 1. Response schemas

Source: `response_schemas/*.json`. All six share the shape `{ agent_name, agent_id, description,
agent_type: "json", accessor: "result", response_schema: { result: {...} }, example_response }`.
Field types below are as declared in `response_schema.result` (all leaf types are declared as
`"string"` or `"number"` -- the schema format has no array/object type annotation, so an
`"instruction": "string"` inside an array means "each item has an `instruction` field of type
string").

### 1.1 Transcription Agent (`transcription_agent_response.json`)

| Field | Type | Notes |
|---|---|---|
| `transcript[]` | array | list of turns |
| `transcript[].turn_number` | number | |
| `transcript[].speaker` | string | |
| `transcript[].timestamp_start` | string | e.g. `"00:04"` |
| `transcript[].timestamp_end` | string | |
| `transcript[].text` | string | |
| `transcript[].language_notes` | string | free text, e.g. code-switching notes |
| `total_duration` | string | e.g. `"00:20"` |
| `speaker_count` | number | |
| `language_detected` | string | |

### 1.2 Process Extraction Agent (`process_extraction_agent_response.json`)

| Field | Type | Notes |
|---|---|---|
| `steps[]` | array | |
| `steps[].step_number` | number | |
| `steps[].action` | string | |
| `steps[].evidence_quote` | string | direct quote from transcript |
| `steps[].timestamp_start` | string | |
| `steps[].timestamp_end` | string | |
| `steps[].prerequisites` | **string** (declared) | see 1.7 for the mismatch with actual demo data, which uses `string[]` |
| `steps[].confidence` | **string** (declared) | demo data uses a `number` (0-1); see 1.7 |
| `total_steps_extracted` | number | |
| `coverage_notes` | string | |

### 1.3 Gap Detection Agent (`gap_detection_agent_response.json`)

| Field | Type | Notes |
|---|---|---|
| `gaps[]` | array | |
| `gaps[].gap_id` | number | |
| `gaps[].related_step` | number | |
| `gaps[].gap_type` | string | e.g. `"verbal_gap"` |
| `gaps[].description` | string | |
| `gaps[].bridging_question` | string | |
| `gaps[].timestamp_start` | string | |
| `gaps[].timestamp_end` | string | |
| `gaps[].severity` | string | e.g. `"high"`, `"critical"` |
| `total_gaps_found` | number | |
| `expertise_blindness_score` | string | free text score, e.g. `"9.2/10 - ..."`, not a bare number |
| `analysis_notes` | string | |

### 1.4 Visual Gap Filler Agent (`visual_gap_filler_agent_response.json`)

| Field | Type | Notes |
|---|---|---|
| `visual_fills[]` | array | |
| `visual_fills[].gap_id` | number | correlates to `gaps[].gap_id` from stage 3 |
| `visual_fills[].related_step` | number | |
| `visual_fills[].frames_analyzed` | number | **fabricated in the current pipeline** -- no frames are ever supplied, see `06-known-gaps.md` entry 1 |
| `visual_fills[].screen_actions` | string | declared string; demo data uses `string[]`, same mismatch pattern as 1.7 |
| `visual_fills[].ui_elements_identified` | string | declared string; demo data uses `string[]` |
| `visual_fills[].navigation_path` | string | |
| `visual_fills[].data_entered` | string | declared string; demo data uses `string \| null` |
| `visual_fills[].visual_step_description` | string | |
| `visual_fills[].confidence` | string | declared string; demo data uses `number` |
| `total_gaps_filled` | number | |
| `analysis_summary` | string | |

### 1.5 Documentation Synthesis Agent (`documentation_synthesis_agent_response.json`)

The only agent with `has_module_outputs: true` and `module_outputs_example: { artifact_files:
[{ file_url }] }`. Also the only agent with `file_output: true` in `workflow_state.json:44`.

| Field | Type | Notes |
|---|---|---|
| `sop_title` | string | |
| `sop_sections[]` | array | |
| `sop_sections[].section_number` | number | |
| `sop_sections[].section_title` | string | |
| `sop_sections[].steps[]` | array | nested inside each section |
| `sop_sections[].steps[].step_number` | number | |
| `sop_sections[].steps[].instruction` | string | |
| `sop_sections[].steps[].annotation_type` | string | drives UI colour-coding, see `05-frontend-behavior.md` -- observed values `"verbal_gap"`, `"original"` |
| `sop_sections[].steps[].evidence_quote` | string | |
| `sop_sections[].steps[].bridging_explanation` | string | nullable in practice (demo data has `null` for `annotation_type: "original"` steps) |
| `sop_sections[].steps[].source` | string | observed values `"gap_detection"`, `"transcript"` |
| `sop_sections[].steps[].added_in_pass` | number | intended to track which judge pass added the step; see gap register / target spec for the fact that this is currently always `1` because the loop never runs more than once automatically |
| `gap_report[]` | array | standalone, mirrors `sop_sections[].steps[]` gap annotations but flattened |
| `gap_report[].gap_id` | number | |
| `gap_report[].type` | string | |
| `gap_report[].related_step` | number | |
| `gap_report[].description` | string | |
| `gap_report[].resolution` | string | |
| `gap_report[].timestamp` | string | |
| `metadata.total_original_steps` | number | |
| `metadata.total_gaps_found` | number | |
| `metadata.total_visual_recoveries` | number | |
| `metadata.output_tone` | string | one of the four values in `05-frontend-behavior.md` |

### 1.6 SOP Judge Agent (`sop_judge_agent_response.json`)

| Field | Type | Notes |
|---|---|---|
| `verdict` | string | the only two values the code branches on: `"APPROVED"`, `"GAPS_REMAINING"` (`page.tsx:389`, `workflow.json:29-31`) |
| `confidence_score` | number | rendered as `Math.round(score * 100)%` (`page.tsx:384`, `OutputPanel.tsx:284`) -- assumes a 0-1 range |
| `evaluation.logical_sequencing` | string | |
| `evaluation.implicit_assumptions` | string | |
| `evaluation.prerequisite_completeness` | string | |
| `evaluation.decision_criteria` | string | |
| `evaluation.edge_cases` | string | |
| `findings[]` | array | |
| `findings[].finding_id` | number | |
| `findings[].severity` | string | |
| `findings[].related_step` | number | |
| `findings[].issue` | string | |
| `findings[].recommendation` | string | |
| `iteration_notes` | string | |
| `approval_conditions` | string | |

`findings[]` is the payload carried forward into the next gap-detection prompt as
`prevJudgeFindings` (`page.tsx:391`), i.e. it is the mechanism by which judge feedback is supposed
to shape the next pass -- see `03-agent-pipeline.md` section 3 for why that next pass requires a
manual click.

### 1.7 Schema-vs-reality mismatch

`app/demoData.ts:51-119` (`DEMO_EXTRACTION_RESULT`) declares `prerequisites` as a `string[]`
(e.g. `['Excel file must be open']`, `demoData.ts:59`) and `confidence` as a `number` (e.g. `0.7`,
`demoData.ts:60`), while `response_schemas/process_extraction_agent_response.json` declares both as
`"string"`. The same pattern repeats in `DEMO_VISUAL_FILLER_RESULT`
(`app/demoData.ts:199-259`): `screen_actions`, `ui_elements_identified` are arrays of strings in the
demo data (e.g. `demoData.ts:205`) but declared as bare `"string"` in the schema; `confidence` is
again a `number` in demo data (`demoData.ts:210`) vs `"string"` in schema; `data_entered` is
`string | null` in demo data (`demoData.ts:208, 241`) vs `"string"` in schema. The UI code that
renders these fields (`OutputPanel.tsx`) treats them loosely (`String(v ?? '')`,
`Array.isArray(...)` checks) and tolerates either shape, which is why the mismatch has not caused a
visible crash -- but it means the schema files do not actually describe what the app expects or
produces.

## 2. The four-layer normalization stack

Real Lyzr agent output is free-form model text that is supposed to contain JSON. It survives four
separate, independently-implemented unwrap/coercion passes before a React component ever reads a
field off it. Each layer exists because the layer before it isn't trusted to fully succeed.

### Layer 1 -- `parseLLMJson` (`lib/jsonParser.ts`, 344 lines, runs server-side in the agent route)

Input: the raw agent response text (or object). Output: parsed JSON, or a
`{ success: false, data: null, error, rawJson: null }` failure object.

Steps, in order:
1. `coerceToTextIfNeeded` -- if the input isn't already a string, tries to pull text out of known
   LLM wrapper shapes first (`x.content[]`, `x.choices[]`, `x.message`, `x.text`, or an array of
   message-like objects), else falls back to `JSON.stringify` (`jsonParser.ts:44-60, 62-84`).
2. `fixCommonJsonIssues` -- a chain of regex repairs: strip BOM, strip `//` and `/* */` comments,
   strip `#` line comments, normalize curly quotes to straight quotes, strip trailing commas, quote
   bare object keys, convert single-quoted strings to double-quoted, convert Python literals
   `True`/`False`/`None`/`undefined` to JSON equivalents, strip `...` and unicode ellipsis
   (`jsonParser.ts:91-115`).
3. `decodeJsonStringLoop` -- if a parsed value is itself a string that looks like JSON (starts with
   a quote, `{`, or `[`), recursively re-parses it, up to `maxDecodeDepth = 3`
   (`jsonParser.ts:117-138`).
4. `findJsonBoundaries` -- a hand-written brace/bracket-depth scanner that tracks quote state to
   find the first balanced `{...}` or `[...]` span in arbitrary surrounding text
   (`jsonParser.ts:140-182`).
5. `extractJson` -- collects up to `maxBlocks = 8` JSON candidates from: fenced ` ```json ` blocks
   (priority 1), any fenced code block starting with `{`/`[` (priority 2), inline single-backtick
   spans (priority 3), and the boundary-scanner result (priority 4); dedupes, sorts by priority if
   `preferFirst` (`jsonParser.ts:184-228`).
6. `tryParseJson` -- attempts `JSON.parse` directly, then via `findJsonBoundaries`, then after
   `fixCommonJsonIssues`, then boundaries again on the fixed string (`jsonParser.ts:230-261`).
7. `unwrapResponse` -- once something parses, if the result isn't already
   `isFinalAgentResponse`-shaped (has `status` + `result`/`message`, or a non-empty `result`
   object), it walks up to `maxUnwrapDepth = 6` levels through a fixed list of wrapper keys
   (`raw_response`, `rawResponse`, `response`, `data`, `result`, `output`, `content`, `message`,
   `text`, `completion`), re-parsing any string value it finds under one of those keys
   (`jsonParser.ts:263-310`).

Overall entry point tries, in order: fenced-```json``` extraction, direct parse, each candidate from
`extractJson` sorted longest-first, then one more aggressive boundary-scan attempt
(`jsonParser.ts:312-344`).

### Layer 2 -- `normalizeResponse` (`app/api/agent/route.ts:42-119`, runs server-side)

Takes Layer 1's output (or the raw response if Layer 1 failed in a specific way -- see below) and
coerces it into exactly `{ status: 'success'|'error', result: object, message?: string, metadata?:
object }` via seven branches, checked in order:
1. Falsy input -> `{status: 'error', result: {}, message: 'Empty response from agent'}`.
2. A plain string -> `{status: 'success', result: {text: parsed}, message: parsed}`.
3. Non-object, non-string (number/bool) -> wraps as `{result: {value: parsed}}`.
4. Has both `status` and `result` keys -> passes through, `status` forced to `'error'` only if
   explicitly `'error'`, else `'success'`.
5. Has `status` but no `result` -> spreads remaining keys into `result`.
6. Has `result` but no `status` -> unwraps `result`, and if it's a string, extracts a display
   `message` by first checking `parsed.message`, then trying `r.text`, `r.message`, `r.response`,
   `r.answer`, `r.summary`, `r.content` in that order if `result` is itself an object.
7. Has `message: string` but neither of the above -> `{result: {text: message}, message}`.
8. Has `response` key -> recurses into `normalizeResponse(parsed.response)`.
9. Fallback -> `{status: 'success', result: parsed}`.

Called from `pollTask` (`route.ts:317`) with `toNormalize` selected as either the freshly
`parseLLMJson`-parsed value, or (if that parse produced `{success:false, data:null}`) the raw
envelope text instead (`route.ts:312-315`) -- a fallback specifically for the case where Layer 1
itself failed to find JSON.

### Layer 3 -- `parseAgentResult` (`app/page.tsx:190-204`, runs client-side)

Documented fully in `03-agent-pipeline.md` section 7. Re-checks `result.response.result`, falls
back to re-parsing `result.response` as a JSON string, falls back to `result.response` itself.

### Layer 4 -- `extractText` (`lib/aiAgent.ts:304-315`, available but not used by `page.tsx`)

A convenience function exported from `lib/aiAgent.ts` that checks, in order:
`response.message`, `response.result.text`, `.message`, `.response`, `.answer`, `.answer_text`,
`.summary`, `.content`, then `typeof response.result === 'string'`. Not called anywhere in
`page.tsx` (which uses `parseAgentResult` instead), but present as a documented public API of
`lib/aiAgent.ts` and referenced in `CLAUDE.md`'s example usage.

## 3. Where the artifact file surfaces

Only the Documentation Synthesis Agent can produce `module_outputs.artifact_files[]`
(`ArtifactFile: { file_url, name, format_type }`, `lib/aiAgent.ts:35-39`). The client checks
`r5?.module_outputs?.artifact_files` after the synthesis call and, if non-empty, stores
`files[0].file_url` into `artifactUrl` state (`page.tsx:363-364`). `OutputPanel.tsx:161-167` renders
a "Download File" link only when `artifactUrl` is truthy.

## 4. Practical implication for the Python rebuild

Four independent, partially-overlapping shape-guessing layers is strong evidence the agents do not
reliably return their declared JSON schema, and that no layer alone was judged sufficient by
whoever wrote the next one. `07-target-architecture.md` and `08-target-pipeline-spec.md` propose
replacing this stack with enforced structured output (pydantic models bound to each provider's
native structured-output mode) so a parse failure becomes a typed, visible error rather than a
fifth silent fallback.
