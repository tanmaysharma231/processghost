# 05 - Frontend Behavior (as-is)

The UI as it exists, for anyone porting the UX or replacing it. As-is only.

## 1. State inventory (`app/page.tsx`)

All state lives in one component. No global store, no server persistence, no URL/route state --
reload the tab and everything is gone.

| State | Purpose |
|---|---|
| `file` | Selected `File` object (real or synthetic demo file, see section 6). |
| `tone` | One of four output tone strings, default `'step-by-step'`. |
| `cropEnabled`, `cropRegion {x,y,w,h}` | Crop controls for the Visual Gap Filler prompt; see `06-known-gaps.md` entry 9. |
| `demoMode`, `demoLoading` | Whether the loaded file is the sample scenario. |
| `isRunning` | Global "pipeline busy" flag, disables the Generate button. |
| `nodeStatuses: NodeStatus[6]` | Per-agent-node status, drives `ProgressTracker`. |
| `activeAgentId` | Which agent_id is currently running, drives the pulsing dot in the agent list footer (`page.tsx:670`). |
| `pipelineError` | Single string, rendered as a red card (`page.tsx:645-649`). |
| `showProgress` | Whether `ProgressTracker` is mounted at all. |
| `sopTitle`, `sopSections`, `gapReport`, `metadata`, `judgeResults[]`, `artifactUrl` | The accumulated output of the synthesis/judge stages. |
| `isComplete` | Whether to show `OutputPanel`. |
| `transcriptData` | Kept for the transcript-download button in `ProgressTracker`. |
| `judgePassCount`, `awaitingJudgeDecision` | Judge-loop UI control, see `03-agent-pipeline.md` section 3. |
| `pipelineDataRef` (a `useRef`, not state) | Carries `transcriptData`, `stepsData`, `prevJudgeFindings` across renders so `runJudgePass` can be re-invoked later by a button click without re-running earlier stages. |
| `debugOpen`, `debugLogs[]` | Debug console panel. |

## 2. Implicit state machine

Not modeled explicitly as a state enum; reconstructed from the guards used to conditionally render
(`page.tsx:613-663`):

```
idle (no file, !showProgress)
  -> (file selected or demo loaded)
idle-with-file (!showProgress, !isComplete)
  -> (Generate clicked) runPipeline()
uploading / node-0..1 active (showProgress=true, isRunning=true)
  -> runJudgePass(1)
judge-pass-active (nodes 2-5 cycling active/complete)
  -> judge verdict
       GAPS_REMAINING & pass<3 -> awaitingJudgeDecision=true, WAITS for user
                                    -> (Run Another Pass clicked) runJudgePass(n+1) -> back to judge-pass-active
       APPROVED | pass==3       -> isComplete=true
complete (OutputPanel shown)
  -> (New Walkthrough clicked) handleNewWalkthrough() resets everything to idle
error (pipelineError set) -- can occur from any active state, does not clear other state automatically
```

`handleNewWalkthrough` (`page.tsx:575-598`) is the only full reset path; it clears all seventeen
pieces of state and the ref in one call.

## 3. `NodeStatus` and the six-node tracker

`type NodeStatus = 'pending' | 'active' | 'complete' | 'looping' | 'error' | 'waiting'`
(`ProgressTracker.tsx:7`). Six nodes, labels `['Transcription', 'Extraction', 'Gap Detection',
'Visual Analysis', 'Synthesis', 'Judge']` (`ProgressTracker.tsx:19-26`), rendered as a horizontal
strip with connecting lines that fill in as each node completes (`ProgressTracker.tsx:82-108`).

Status-to-visual mapping (`ProgressTracker.tsx:54-80`): `complete`/`active`/`looping` all render a
solid dark square (icon differs: check / spinner / refresh-spin); `waiting` renders a pale yellow
square with a pulsing dot; `error` renders solid red with an alert icon; `pending` renders an
outlined square with a plain dot.

`'looping'` is a fully defined and styled status that **no code path ever sets** -- `updateNode`
(`page.tsx:281-283`) is only ever called with `'active'`, `'complete'`, `'pending'`, or `'waiting'`
(node 5 only, `page.tsx:392`). It exists in the type and the renderer but is dead in practice.

A transcript-download button appears next to node 0 once it completes and `transcriptData` is set
(`ProgressTracker.tsx:92-101`), exporting via `Blob`/`URL.createObjectURL` as
`transcript_{ISO-timestamp}.txt`.

Below the node strip: a pass counter (`"Pass N of 3 used"`) once `judgePassCount > 0`
(`ProgressTracker.tsx:111-119`), and the "Run Another Pass" button when
`awaitingJudgeDecision`, disabled once `judgePassCount >= maxJudgePasses`
(`ProgressTracker.tsx:121-136`).

## 4. Section-by-section

### `HeaderSection.tsx`
Static title ("ProcessGhost"), tagline ("Surface the knowledge experts forgot to write down -- even
what they forgot to say."), and a demo-mode badge shown when `demoMode` is true.

### `HowToUse.tsx`
Static 4-step onboarding card (Load Sample Data / Hit Generate / Review Output / Check Debug
Panel), only rendered before a run starts (`page.tsx:613-615`). Includes a footer tip stating
plainly: "The sample data runs a fully hardcoded demo with instant results -- no API calls are
made." This is the app itself disclosing the fact recorded as gap register entry 2.

### `UploadSection.tsx`
- Dropzone accepting `.mp4/.webm/.mov` via drag-drop or click (`UploadSection.tsx:38-47, 73-79`);
  also matches `.mp3/.wav/.xlsx/.xls` in the drop-handler regex (`UploadSection.tsx:41`) though the
  visible file-picker `accept` attribute only lists video MIME types (`UploadSection.tsx:76`).
- "Try with Sample Data" button, disabled while `demoLoading || isRunning`.
- Once a file is present: file-info card (name, formatted size), remove button, and (if demo mode)
  a note naming the two preloaded demo files.
- Crop Region controls: a "Full Frame" switch (inverts `cropEnabled`) and, when enabled, four
  number inputs `x/y/w/h` in percent (`UploadSection.tsx:145-161`).
- Output Tone select: four options, values `step-by-step` / `formal` / `wiki` / `bulleted`, labels
  "Step-by-Step Instructional" / "Formal / Corporate" / "Conversational Wiki" / "Bulleted /
  Scannable" (`UploadSection.tsx:171-175`).
- "Generate SOP" button, disabled when `(!file && !demoMode) || isRunning`.

### `ProgressTracker.tsx`
Covered in section 3 above.

### `OutputPanel.tsx`
- Header: SOP title, and (if `metadata` present) three badges: step count, gap count, visual-fill
  count.
- Action row: Copy to Clipboard (via `lib/clipboard.ts`, iframe-safe), Download Markdown (builds a
  `.md` file via `buildMarkdownText`, `OutputPanel.tsx:94-114`), Download File (only if
  `artifactUrl` set), New Walkthrough.
- Two tabs: "Clean SOP" and "Gap Report (N)".
  - Clean SOP tab: sections -> steps, each step rendered as a card with a coloured left border by
    `annotation_type` via `getAnnotationStyle` (`OutputPanel.tsx:80-92`): `verbal` ->
    red/`AlertTriangle`/"Verbal Gap"; `visual` -> black/`Monitor`/"Visual Recovery"; `judge` ->
    blue/`Search`/"Judge-Flagged"; anything else -> no border, no icon, no label. Also shows the
    `evidence_quote` as a blockquote, `bridging_explanation` with the type icon, and badges for
    `source` and `added_in_pass` (only shown if `> 1`, which per `03-agent-pipeline.md` section 3
    never currently happens with live data since the automatic loop doesn't run).
  - Gap Report tab: one card per gap, badges for step number and type (with the same
    verbal/visual/judge icon logic), description, and resolution if present.
- Collapsible "Review Log (N iterations)" at the bottom: one card per judge pass, showing verdict
  badge, confidence percent, the `evaluation` object rendered as label:value pairs, any `findings[]`
  each in its own bordered box, `iteration_notes`, `approval_conditions`.

### `DebugPanel.tsx`
A slide-out, fixed-position, terminal-styled panel (black background, green/cyan/yellow/red
monospace text) toggled by a floating button. Not gated behind any dev-mode flag -- always
available in production. Each `DebugLogEntry` has a `type` (`agent_start | agent_input |
agent_output | agent_complete | handoff | error | info | loop`) mapped to a colour and a bracketed
prefix (`[START]`, `[INPUT]`, etc., `DebugPanel.tsx:31-51`). Payloads over 500 characters are
truncated with an "Expand full payload" toggle per entry (`DebugPanel.tsx:23-26, 79-93`). `handoff`
entries get a dashed separator line rendered before and after. "Copy Logs" button flattens all
entries to plain text via `lib/clipboard.ts`. Auto-scrolls to bottom on new entries while open
(`DebugPanel.tsx:107-111`).

This is the most carefully designed piece of the UI -- a real-time, typed, timestamped audit trail
of every agent call, including full input/output payloads. `07-target-architecture.md` proposes
promoting this concept from a UI nicety into the backend's actual persisted run record.

## 5. Output tone

Four values (section 4, `UploadSection.tsx:171-175`), passed as a bare string into the synthesis
prompt template (`tone` parameter of `DEMO_PROMPTS.synthesis` and the equivalent live prompt at
`page.tsx:354`). No agent-side validation of the value; an arbitrary string would flow straight
through.

## 6. Demo mode

Two distinct demo code paths exist, and only one of them is actually reachable through the UI:

- `loadDemoData()` (`page.tsx:285-295`): creates a synthetic `File` (`new Blob(['demo-walkthrough-
  video'], {type: 'video/mp4'})` named `"english_hackathon_test.mp4 + ProcessGhost_Bills_Tracker
  .xlsx"`), sets `demoMode = true`, resets tone/crop to defaults. This is what "Try with Sample
  Data" calls (`UploadSection.tsx:91`, wired via `onLoadDemo` prop from `page.tsx:630`).
- `runPipeline()` special-cases `demoMode` at its very top (`page.tsx:490-494`): if `demoMode` is
  true, it calls `runDemoPipeline()` and returns immediately -- the entire upload/agent-call branch
  below it is skipped.
- `runDemoPipeline()` (`page.tsx:418-485`): resets all pipeline state, calls
  `generateDemoDebugLogs()` (`demoData.ts:457-514`) to build 37 fake, pre-timestamped log entries
  with fabricated token-usage numbers, then loops over the six nodes with fixed delays
  `[300, 350, 400, 450, 500, 400]` ms (`page.tsx:437`), setting each to `active` then `complete`
  via `await delay(ms)` and appending a hardcoded slice of the 37 log entries per node
  (`page.tsx:438-464`). After the loop, it assigns `transcriptData = DEMO_TRANSCRIPT_RESULT`,
  `sopTitle/sopSections/gapReport/metadata = DEMO_SOP_RESULT.*`, `judgeResults = [DEMO_JUDGE_RESULT]`,
  `judgePassCount = 1`, and `isComplete = true`.

**No `callAIAgent` call occurs anywhere in this path.** `fetch` is never invoked. The entire
"generate SOP" experience for demo mode is `setTimeout` plus assignment from
`app/demoData.ts`. The five `DEMO_PROMPTS` templates documented in `03-agent-pipeline.md` section
5a are therefore unreachable dead code under the current control flow, since the only way
`demoMode` is ever `true` is via `loadDemoData`, and `runPipeline` intercepts that case before it
would reach any `callAIAgent` call.

`app/demoData.ts` content itself (`DEMO_TRANSCRIPT_RESULT`, `DEMO_EXTRACTION_RESULT`,
`DEMO_GAP_DETECTION_RESULT`, `DEMO_VISUAL_FILLER_RESULT`, `DEMO_SOP_RESULT`, `DEMO_JUDGE_RESULT`) is
a single, fully worked, internally consistent example: a 20-second, one-speaker walkthrough
producing 7 extracted steps, 7 detected gaps (all `severity: 'critical'` or `'high'`), 5 of those
gaps visually recovered, synthesized into a 6-section / 8-step SOP, and approved by the judge on
the first pass with `confidence_score: 0.94` and zero findings. Only `DEMO_TRANSCRIPT_RESULT`,
`DEMO_SOP_RESULT`, and `DEMO_JUDGE_RESULT` are actually consumed by `runDemoPipeline`;
`DEMO_EXTRACTION_RESULT`, `DEMO_GAP_DETECTION_RESULT`, and `DEMO_VISUAL_FILLER_RESULT` are defined
and exported but never referenced from `page.tsx` -- they exist only as narrative/documentation
value and as raw material for the debug log text.

See `06-known-gaps.md` entry 2 for why this matters, and `10-migration-map.md` for why this same
data is nonetheless worth preserving as a test fixture in the rebuild.
