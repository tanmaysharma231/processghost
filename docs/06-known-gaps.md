# 06 - Known Gaps and Risks

The defect and risk register for the as-is system. Ordered by severity. Each entry states what the
code does, what it claims (if applicable), the evidence, the impact, and whether the Python rebuild
must address it. This is the single "if you only read one thing" document for the base repo.

---

### 1. The vision agent is blind -- CRITICAL, must fix

**Claim.** The Visual Gap Filler Agent's own schema description: "Receives descriptions of cropped
video frames from gap timestamp windows. Analyzes frame descriptions via vision to identify
unverbalized screen actions -- buttons clicked, menus navigated, forms filled."
(`response_schemas/visual_gap_filler_agent_response.json`).

**Reality.** The call site (`app/page.tsx:328-343`) builds a text-only prompt describing the
spreadsheet from developer-written prose (`page.tsx:116-127` for the demo variant, `page.tsx:331-
336` for the live variant) and invokes `callAIAgent(visualMsg, AGENTS.visualFiller)` with **no
`assets` option**. Compare to the Transcription call (`page.tsx:529`), which is the only call in
the file that passes `assets`. No frame of video is ever extracted from the uploaded file; no image
is ever sent to any agent. The output field `frames_analyzed` (declared as a `number` in the
schema) is therefore a number the model invents, not a count of anything actually processed.

**Impact.** This is the pipeline's central differentiator -- "recover what the expert did but did
not say" -- and it is not implemented. Everything the Visual Gap Filler currently outputs is the
model reasoning from a text description the developer wrote, not from observation.

**Fix required.** Yes. Design in `09-visual-gap-filler.md`.

---

### 2. Demo mode is a complete illusion -- CRITICAL, must fix if demo mode is kept

**Reality.** `runDemoPipeline()` (`app/page.tsx:418-485`) makes zero `fetch` calls. It animates
node statuses on fixed `setTimeout` delays and then assigns hardcoded constants from
`app/demoData.ts` (514 lines) directly into component state. The app's own `HowToUse.tsx` tip
admits this outright: "The sample data runs a fully hardcoded demo with instant results -- no API
calls are made."

**Impact.** "Try with Sample Data" is presented as step 1 of onboarding (`HowToUse.tsx`) and is the
only zero-friction way to evaluate the app. It cannot reveal a broken prompt, an expired
`agent_id`, a missing `LYZR_API_KEY`, a schema drift, a rate limit, or any other real failure mode
-- it will "work" identically whether or not the backend, the API key, or the agents are even
configured. Anyone who evaluates ProcessGhost via the demo has learned nothing about whether the
pipeline functions.

**Fix required.** Yes, if a demo/sample-data path is kept at all in the rebuild -- see
`10-migration-map.md` ("recorded real-response fixtures") for the proposed replacement.

---

### 3. The judge loop is not a loop -- HIGH, must fix

**Claim.** `workflow.json:26-34` declares an automatic conditional edge:
`judge_decision.GAPS_REMAINING -> gap_detection_agent`. Agent 3's own schema description says it
reads "Judge findings on loop iterations" -- language that presumes automatic re-entry.

**Reality.** `runJudgePass` (`app/page.tsx:298-411`), on `verdict === 'GAPS_REMAINING'` and
`passNumber < MAX_JUDGE_PASSES`, stores the findings, sets `awaitingJudgeDecision = true`, and
returns. Nothing calls `runJudgePass` again until a human clicks "Run Another Pass"
(`ProgressTracker.tsx:121-136` -> `page.tsx:569-573`).

**Impact.** The declared self-correcting loop -- the mechanism that is supposed to make the SOP
converge on completeness -- requires a human in the loop for every iteration. `MAX_JUDGE_PASSES =
3` (`page.tsx:46`) caps total judge evaluations, not automatic pipeline iterations, since there is
never more than one automatic iteration.

**Fix required.** Yes. Target loop semantics in `08-target-pipeline-spec.md`.

---

### 4. Video reaches only the first agent -- HIGH, must fix (subset of #1's root cause)

**Reality.** `uploadedAssetIds` from `uploadFiles(file)` is passed only to the Transcription call
(`page.tsx:529`). Every subsequent agent (Extraction, Gap Detection, Visual Gap Filler, Synthesis,
Judge) receives only JSON text built from the previous agent's parsed output, via `safeStringify`.

**Impact.** Beyond agent 1, the pipeline is a pure text-relay chain over the transcript and its
derivatives -- nothing downstream ever has access to the source video or spreadsheet file.

**Fix required.** Yes, specifically for the visual-recovery stage -- see entry 1 and
`09-visual-gap-filler.md`.

---

### 5. Silent, lossy truncation between every stage -- HIGH, should fix

**Reality.** `safeStringify` (`page.tsx:156-180`) caps at 15,000 characters per field and, when
summarizing, keeps only the **first 10 items** of any array and truncates any string over 2,000
characters. `capMessage` (`page.tsx:185-188`) caps the final assembled prompt at 45,000 characters.
Neither truncation is logged to the user-visible UI (only the raw truncated payload appears in the
Debug Panel, which is off by default and not something an end user is expected to inspect).

**Impact.** A process with more than ~10 steps, gaps, or transcript turns will silently lose data
somewhere mid-pipeline with no indication in the final SOP or in any error state that anything was
dropped. This directly undermines the product's core promise of completeness.

**Fix required.** Yes -- replace with explicit, visible context budgeting. See
`08-target-pipeline-spec.md`.

---

### 6. Prompts are welded to one demo scenario; the live pipeline is materially weaker -- MEDIUM, should fix

**Reality.** Compare `03-agent-pipeline.md` sections 5a and 5b. The demo prompt templates
(`DEMO_PROMPTS`, `page.tsx:79-149`) embed extensive scenario knowledge -- including, in
`gapDetection`, a "CRITICAL CONTEXT" block that lists the five gaps to find *inside the prompt*.
The live (non-demo) prompts are one-line templates with no scenario grounding at all
(`page.tsx:527, 545, 313-315, 334-335, 354, 378`).

**Impact.** Two consequences: (a) the demo prompt for gap detection is not actually testing the
agent's ability to find gaps, since the answer is supplied in the prompt; and (b) as established in
entry 2 and `05-frontend-behavior.md` section 6, these demo prompts are additionally unreachable
dead code under the current control flow (`runDemoPipeline` short-circuits before any agent call
happens when `demoMode` is true), so the *only* prompts ever actually sent to a live agent are the
weak, unguided ones in section 5b.

**Fix required.** Yes -- prompts should be versioned, scenario-agnostic, and not leak answers into
gap-finding prompts. See `08-target-pipeline-spec.md`.

---

### 7. Four layers of defensive JSON unwrapping -- MEDIUM, should fix

**Reality.** `parseLLMJson` (344 lines) -> `normalizeResponse` (server) -> `parseAgentResult`
(client) -> `extractText` (available, unused). Full detail in `04-data-contracts.md`.

**Impact.** This much defensive parsing strongly suggests the agents do not reliably return their
declared JSON schema on the first try, and no single layer was trusted enough to be the last one
written. Debugging a malformed response requires reasoning through four independent unwrap
implementations.

**Fix required.** Yes -- enforce structured output at the model-call boundary (pydantic /
provider-native structured output) so schema drift becomes a typed, visible error instead of a
fifth silent fallback. See `07-target-architecture.md`.

---

### 8. Client-side orchestration with no persistence -- MEDIUM, should fix

**Reality.** The entire pipeline state lives in one React component's `useState`/`useRef`. Closing
or reloading the tab loses everything -- there is no server-side record of a run, no resume
capability, no way to inspect a past run after the fact except by having kept the Debug Panel open
and manually copying its log.

**Impact.** No auditability beyond a single browser session; no ability to run the pipeline
unattended (e.g. batch-process a folder of walkthroughs) or continue a long-running job after a
disconnect.

**Fix required.** Yes -- server-side run persistence is a core target-architecture decision. See
`07-target-architecture.md` ("Run model").

---

### 9. Crop UI is decorative -- LOW, should fix or remove

**Reality.** The x/y/w/h crop controls (`UploadSection.tsx:145-161`) only ever get interpolated
into a text string appended to the Visual Gap Filler prompt (`" Crop: x=...% y=...% w=...% h=...%"`,
`page.tsx:334`). No image is cropped because no image is sent at all (entry 1). The control
currently cannot do anything.

**Impact.** Misleading affordance -- a user setting crop coordinates believes they are constraining
what the vision agent looks at; nothing changes as a result.

**Fix required.** Yes, once entry 1 is fixed the crop control should operate on real extracted
frames (see `09-visual-gap-filler.md`); until then it should not be presented as functional.

---

### 10. Build checks disabled -- LOW, should fix if this app is kept running

**Reality.** `next.config.js:11-18`: `typescript.ignoreBuildErrors: true` and
`eslint.ignoreDuringBuilds: true`.

**Impact.** Type errors and lint violations can reach production silently. Given the schema
mismatches documented in `04-data-contracts.md` section 1.7, this is not a hypothetical risk.

**Fix required.** Moot for the Python rebuild target (see `11-open-questions.md` on whether the
Next.js app is kept at all); noted for completeness.

---

### 11. Project violates its own coding rules -- LOW, informational

**Reality.** `CLAUDE.md:13` mandates `lucide-react` ONLY, never `react-icons`. All five
`app/sections/*.tsx` files (`DebugPanel.tsx:4`, `HeaderSection.tsx:3`, `HowToUse.tsx:4`,
`OutputPanel.tsx:10`, `ProgressTracker.tsx:3`, `UploadSection.tsx:10`) import from `react-icons/fi`
or `react-icons/ri`.

**Impact.** None functionally (the package is listed as a dependency, `package.json:54`), but it
indicates the generator's own stated constraints were not enforced on its own output.

**Fix required.** No -- informational only, moot for a Python rebuild.

---

### 12. Secrets, ops, and resilience gaps -- LOW to MEDIUM, address opportunistically

**Reality, collected:**
- No `.env.local` present in the repo; only `.env.example`. The app cannot run without one being
  created and populated with a real `LYZR_API_KEY`.
- `LYZR_API_KEY` is the sole credential; there is no per-user auth model anywhere in the app
  (`user_id`/`session_id` are just random UUID-based strings, `app/api/agent/route.ts:34-40,
  192-193`).
- No retry/backoff on Lyzr 5xx responses beyond the client's poll loop tolerating `'processing'`;
  a submit-time 5xx or 4xx is surfaced immediately as a pipeline failure (`route.ts:215-236`).
- No rate limiting on either API route.
- Fixed 5-minute client-side timeout (`POLL_TIMEOUT_MS`, `lib/aiAgent.ts:78`) on a step that may
  need to transcribe an arbitrarily long video; no server-side task cancellation is sent to Lyzr
  when the client gives up.

**Impact.** Fine for a demo/hackathon deployment; not production-grade for unattended or
multi-user use.

**Fix required.** Address as part of the target architecture's operational design; not a design
blocker. See `07-target-architecture.md` and `11-open-questions.md`.
