# 03 - Agent Pipeline (as-is)

The six agents, the declared graph, and what the code actually executes. As-is only; the gap
between declaration and execution is documented here as fact and cross-referenced in
`06-known-gaps.md`.

## 1. The six agents

| # | Agent | Provider | Model | temp | top_p | Lyzr `agent_id` |
|---|---|---|---|---|---|---|
| 1 | Transcription Agent | Google | gemini-2.5-pro | 0.2 | 1 | `69ec5b0720c378a25b45e8d2` |
| 2 | Process Extraction Agent | Anthropic | claude-sonnet-4-6 | 0.3 | 1 | `69ec5b33603f5bad913c0dbd` |
| 3 | Gap Detection Agent | Anthropic | claude-sonnet-4-6 | 0.3 | 1 | `69ec5b3307db2da1f5e0c20c` |
| 4 | Visual Gap Filler Agent | Google | gemini-2.5-pro | 0.3 | 1 | `69ec5b349291081b210b36ea` |
| 5 | Documentation Synthesis Agent | OpenAI | gpt-4.1 | 0.4 | 0.95 | `69ec5b5bf7d0c95f8d9bf0b8` |
| 6 | SOP Judge Agent | Anthropic | claude-sonnet-4-6 | 0.2 | 1 | `69ec5b5b9291081b210b36f8` |

Source: `workflow_state.json:4-49`. The same six IDs are hardcoded again independently in
`app/page.tsx:19-26` as the `AGENTS` object -- there is no single source of truth; if one file's ID
were edited the other would silently diverge.

Agent 5 has `file_output: true` (`workflow_state.json:44`) -- it is the only agent whose response
can include `module_outputs.artifact_files` (see `04-data-contracts.md`).

Stated purpose of each agent (from `response_schemas/*.json` `description` fields, verbatim):

1. **Transcription**: "Extracts audio from uploaded video. Transcribes with Hinglish/English
   code-switching handling. Labels multiple speakers. Outputs structured transcript with numbered
   speaker turns and timestamps per turn."
2. **Process Extraction**: "Reads timestamped transcript. Extracts only explicitly stated process
   steps. Outputs numbered step list with direct quote evidence and timestamp range per step."
3. **Gap Detection**: "Reads raw transcript and step list plus Judge findings on loop iterations.
   Identifies expertise blindness -- skipped prerequisites, vague references, implied knowledge.
   Outputs gap details with bridging questions and timestamp windows."
4. **Visual Gap Filler**: "Receives descriptions of cropped video frames from gap timestamp windows.
   Analyzes frame descriptions via vision to identify unverbalized screen actions -- buttons
   clicked, menus navigated, forms filled. Outputs structured visual step description per gap."
5. **Documentation Synthesis**: "Takes step list, gap report, and visual fills. Produces draft SOP
   with inline gap annotations (verbal gaps and visual recoveries) and standalone gap report.
   Supports multiple output tones."
6. **SOP Judge**: "Reviews the complete draft SOP as a senior process auditor persona. Evaluates
   logical sequencing, remaining implicit assumptions, and new-hire readiness. Outputs structured
   verdict: APPROVED or GAPS_REMAINING with specific findings."

Agent 3's own description explicitly says it reads "Judge findings on loop iterations" -- i.e. the
schema itself assumes the loop in section 3 below is automatic and repeated. Agent 4's description
claims it receives visual frame data; see `06-known-gaps.md` entry 1 for how that compares to the
actual invocation in section 4.

## 2. Declared graph

From `workflow.json:2-34`:

```
input_node -> transcription_agent -> extraction_agent -> gap_detection_agent
  -> visual_gap_filler_agent -> synthesis_agent -> judge_agent -> judge_decision

judge_decision:
  condition_field: result.verdict
  GAPS_REMAINING -> gap_detection_agent   (loop back)
  APPROVED       -> output_node
```

This is a standard "generate, critique, revise" loop shape: judge feedback should route back into
gap detection and re-run gap_detection -> visual_gap_filler -> synthesis -> judge until approved.

## 3. What the code actually executes

`runJudgePass(passNumber)` (`app/page.tsx:298-411`) is the unit that implements agents 3-6. Called
first as `runJudgePass(1)` from the initial pipeline run (`app/page.tsx:560`), and again from
`handleRunAnotherPass` (`app/page.tsx:569-573`) which only fires on a user click of the "Run Another
Pass" button (`app/sections/ProgressTracker.tsx:121-136`).

Sequence inside one `runJudgePass` call:
1. Gap Detection (`page.tsx:307-322`) -- includes `prevJudgeFindings` from the prior pass if any
   (`page.tsx:311-315`).
2. Visual Gap Filler (`page.tsx:328-343`).
3. Documentation Synthesis (`page.tsx:349-367`) -- also sets `sopTitle`, `sopSections`,
   `gapReport`, `metadata`, and `artifactUrl` from the response here.
4. SOP Judge (`page.tsx:373-385`).
5. Branch on `judgeData?.verdict` (`page.tsx:389-403`):
   - `'GAPS_REMAINING'` and `passNumber < MAX_JUDGE_PASSES` (= 3, `page.tsx:46`): stores
     `judgeData.findings` into `pipelineDataRef.current.prevJudgeFindings` (`page.tsx:391`), sets
     node 5 to `'waiting'`, sets `awaitingJudgeDecision = true` (`page.tsx:392-393`), and resets
     nodes 2-4 to `'pending'` (`page.tsx:396-398`). **The function then returns.** Nothing calls
     `runJudgePass` again automatically.
   - Anything else (`'APPROVED'`, or `GAPS_REMAINING` at the pass cap): marks node 5 `'complete'`
     and `isComplete = true` (`page.tsx:400-401`).

So the declared automatic edge `judge_decision -> gap_detection_agent (GAPS_REMAINING)`
(`workflow.json:23`) is, in the running code, a dead end that waits for a human. The UI surfaces
this as the "Run Another Pass (N remaining)" button. `MAX_JUDGE_PASSES = 3` caps total judge
evaluations at 3, not total loop iterations of the full 6-agent chain (the full chain only ever
runs once, for the transcription+extraction steps).

## 4. Video reaches only the first agent

`uploadFiles(file)` produces `asset_ids`, passed to the Transcription call only:
```
const r1 = await callAIAgent(transcriptionMsg, AGENTS.transcription,
  uploadedAssetIds.length > 0 ? { assets: uploadedAssetIds } : undefined)   // page.tsx:529
```
No other `callAIAgent` invocation in the file passes `assets`. Agents 2 through 6 receive only
JSON-stringified text built from the previous agent's parsed output (see section 6). The Visual
Gap Filler agent -- despite its stated purpose of analyzing "cropped video frames" -- never
receives the video, an image, or a frame. Its prompt (`page.tsx:116-127`, reproduced below)
describes the spreadsheet screen in prose instead.

## 5. Prompt templates, verbatim

### 5a. Demo prompts (`DEMO_PROMPTS`, `app/page.tsx:79-149`)

Used only when `demoMode` is true but the user has *not* clicked "Try with Sample Data"'s instant
path (`runDemoPipeline`, section 7 of `05-frontend-behavior.md`) -- i.e. reachable only if
`demoMode` is set without going through the fully-hardcoded shortcut. In current code, loading demo
data always sets `demoMode = true` (`page.tsx:291`) and `runPipeline` special-cases `demoMode` to
call `runDemoPipeline` directly (`page.tsx:490-494`), which never calls `callAIAgent` at all. So as
written, **these five prompt templates are dead code** -- built but never sent, because the only
way to reach `runJudgePass`/`runPipeline`'s live-agent branch with `demoMode === true` does not
exist in the current control flow. They are preserved here because they are the most complete
statement of the intended scenario and prompt design in the repo, and they are the reference the
Python rebuild's fixture prompts should be based on.

```
transcription:
"Transcribe the following audio walkthrough recording: demo_walkthrough.mp3

DEMO CONTEXT -- This is a real preloaded demo scenario:
The audio is an expert explaining in Hinglish (Hindi-English mix) how to add a new March bill entry
to a company bills tracker spreadsheet (ProcessGhost_Bills_Tracker.xlsx).

The spreadsheet has two sheets:
- "Master Bills" -- 8 recurring vendors (AWS, Slack, Google Workspace, Zoom, HubSpot, Jira, Figma, Notion)
- "Monthly Actuals" -- Jan/Feb complete, March partially filled with 5 pending rows

The expert walks through: opening the file, navigating to the right area, adding vendor name,
entering the amount, setting the date, marking status as paid. The expert speaks casually, mixing
Hindi and English, and skips several steps that feel obvious to them.

Generate a structured transcript capturing all spoken content, noting the Hinglish language, and
timestamping key moments."
```

Note the sheet count/vendor list here (two sheets, 8 vendors) does not match `DEMO_CONTEXT`
(`page.tsx:65-77`, three sheets: Master Bills / Transactions - March / Monthly Actuals) or the
actual hardcoded output in `app/demoData.ts` (also three sheets). This is an internal inconsistency
in the repo, not a transcription error in this doc.

```
extraction(transcriptData):
"Extract explicit process steps from this transcript of a walkthrough for adding a March bill entry
to a company bills tracker spreadsheet.

Transcript: {transcriptData as JSON, safeStringify}

Context: The process involves adding a new row to the "Monthly Actuals" sheet for a March vendor
payment in ProcessGhost_Bills_Tracker.xlsx. Extract every step the expert explicitly mentions."
```

```
gapDetection(transcriptData, stepsData, prevFindings?):
"Detect expertise blindness gaps in this walkthrough for adding a bill entry to a company
spreadsheet tracker.

Transcript: {transcriptData}
Extracted Steps: {stepsData}
[Previous judge findings to address: {prevFindings}]

CRITICAL CONTEXT -- The expert SKIPS these things (known gaps to detect):
1. Which sheet to navigate to -- expert jumps to "Monthly Actuals" without saying the sheet name
2. How to find the correct month's rows -- expert scrolls to March section without explaining the layout structure
3. Status must be typed EXACTLY as "Paid" (case-sensitive) for the SUMIF summary formula to work correctly
4. The bottom summary row contains a SUMIF formula -- new entries must go ABOVE it, never overwrite it
5. The Variance column (Actual vs Budget) calculates automatically via formula and should not be manually edited

These are classic expertise blindness patterns: the expert assumes the viewer knows the spreadsheet
layout, formula dependencies, and exact text requirements. Surface ALL of these gaps."
```

This prompt hands the model the answer key. It is not testing whether the agent can find
expertise-blindness gaps; it is testing whether the agent can restate five gaps it was just told
about. See `06-known-gaps.md` entry 6.

```
visualFiller(gapsData):
"Analyze the spreadsheet screen for visual gap recovery. The file is ProcessGhost_Bills_Tracker.xlsx
viewed in full frame.

Gaps detected: {gapsData}

Context: This is a company bills tracker. The screen shows:
- Tab bar at bottom with "Master Bills" and "Monthly Actuals" sheets
- Monthly Actuals sheet has sections grouped by month (January, February, March)
- Each section has columns: Vendor, Amount, Date, Status, Variance
- A SUMIF summary row at the bottom of each month section
- The Variance column contains formulas (=Budget-Actual)

Recover any unverbalized screen actions the expert performed but did not describe, especially:
sheet tab clicks, scrolling behavior, cell selection patterns, and formula-protected cells visible
on screen."
```

No image, no asset, no frame is attached to this call anywhere in the code. The "screen" is
described entirely in prose supplied by the developer, not observed by the model. See
`06-known-gaps.md` entry 1.

```
synthesis(tone, stepsData, gapsData, visualData):
"Synthesize a comprehensive SOP document for "How to Add a Monthly Bill Entry in ProcessGhost Bills
Tracker" with tone: {tone}.

Process Steps: {stepsData}
Detected Gaps: {gapsData}
Visual Fills: {visualData}

This SOP should guide a new employee through adding a March vendor payment to the company bills
tracker spreadsheet. Include all recovered gaps as annotated steps -- especially the critical
warnings about the SUMIF formula row and case-sensitive "Paid" status."
```

```
judge(sopData):
"Review this draft SOP for adding a bill entry to a company spreadsheet tracker. Evaluate as a
senior process auditor:

{sopData}

Key verification points:
- Does the SOP mention which sheet tab to navigate to?
- Does it explain how to locate the correct month section?
- Does it warn about the case-sensitive "Paid" status for SUMIF?
- Does it warn about NOT overwriting the summary formula row?
- Does it note the auto-calculating Variance column?
- Is the step sequence logical for a first-time user?"
```

### 5b. Live (non-demo) prompts, verbatim

Used whenever a real file is uploaded and `demoMode` is false. These are the prompts an actual user
of the app triggers.

```
Transcription (page.tsx:527):
"Transcribe the following video recording: {file.name}. The video is a screen recording of a
process walkthrough. Please generate a structured transcript."

Extraction (page.tsx:545):
"Extract process steps from this transcript: {safeStringify(transcriptData)}"

Gap Detection, no prior findings (page.tsx:315):
"Detect expertise blindness gaps. Transcript: {safeStringify(tData)} Steps: {safeStringify(stepsData)}"

Gap Detection, with prior findings (page.tsx:313-314):
"Detect expertise blindness gaps. Transcript: {safeStringify(tData)} Steps: {safeStringify(stepsData)}
Judge findings: {safeStringify(prevJudgeFindings)}"

Visual Gap Filler (page.tsx:334-335):
"Analyze gap regions for visual screen actions.{cropInfo} Gaps: {safeStringify(gapsData)}"
  where cropInfo = " Crop: x={x}% y={y}% w={w}% h={h}%" if cropEnabled else " Full frame."

Synthesis (page.tsx:354):
"Synthesize an SOP document with tone: {tone}. Steps: {safeStringify(stepsData)}
Gaps: {safeStringify(gapsData)} Visual Fills: {safeStringify(visualData)}"

Judge (page.tsx:378):
"Review this draft SOP as a senior process auditor: {safeStringify(sopData)}"
```

These live prompts carry none of the scenario-specific guidance the demo prompts have (no mention
of sheet names, formulas, or matching logic) -- they rely entirely on the agent's own reasoning
plus whatever the transcript/extraction actually contains. See `06-known-gaps.md` entry 6 for the
consequence.

## 6. Data truncation helpers used by every prompt above

`safeStringify(data, maxLen = 15000)` (`page.tsx:156-180`): `JSON.stringify`s the input; if the
result exceeds 15,000 characters and the input is a plain object, it rebuilds a "summary" object
that keeps only the **first 10 items** of any array field and truncates any string field longer
than 2000 characters to `2000 chars + '... [truncated]'`; if that summary is still too long, or the
input wasn't an object, it hard-truncates the raw JSON string at 15,000 characters.

`capMessage(msg, maxLen = 45000)` (`page.tsx:185-188`): truncates the final assembled prompt string
at 45,000 characters, appending `\n\n[Message truncated to fit API limits]`.

Both truncations are silent to the end user -- no warning is shown in the UI when data is dropped.
See `06-known-gaps.md` entry 5.

## 7. `parseAgentResult` -- the client-side result unwrap

`app/page.tsx:190-204`. After every `callAIAgent` call, the raw `AIAgentResponse` is passed through
this function before use:
```
resp = result.response
if not resp: return null
r = resp.result
if r is a non-empty object: return r
if resp is a string: return JSON.parse(resp).result ?? JSON.parse(resp)
return resp
```
This is a fourth layer of shape-guessing on top of the three already applied server-side (see
`04-data-contracts.md`). It exists because the pipeline cannot fully trust that
`normalizeResponse`'s output always lands in the expected shape.
