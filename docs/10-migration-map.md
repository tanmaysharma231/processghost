# 10 - Migration Map: TypeScript to Python

Decision by decision: what gets ported, what gets rewritten, what gets dropped, and what gets
preserved as a test fixture. Cross-references `02-current-architecture.md`'s file tree, which this
document classifies exhaustively.

## Port -- concepts and contracts carried over as-is in intent

These represent real design work in the as-is system that has nothing to do with the
TypeScript/Next.js implementation and should transfer directly:

- The six agent role definitions: purpose, model choice, temperature (`workflow_state.json:4-49`)
  -- becomes `agents/roles.py` per `07-target-architecture.md`.
- All six response schemas' field sets (`response_schemas/*.json`) -- becomes the pydantic models
  in `models/`, see field-mapping table below.
- The SOP output structure: sections containing steps, each step carrying
  `annotation_type`/`evidence_quote`/`bridging_explanation`/`source`/`added_in_pass`
  (`documentation_synthesis_agent_response.json`).
- The gap report structure: `gap_id`/`type`/`related_step`/`description`/`resolution`/`timestamp`.
- The judge verdict contract: `verdict` in `{APPROVED, GAPS_REMAINING}`, `confidence_score`,
  structured `evaluation`, `findings[]` feeding the next pass.
- The debug event log concept (`app/sections/DebugPanel.tsx:7-15`) -- becomes the persisted
  `StageExecution` record (`07-target-architecture.md`), not a UI-only feature.
- The markdown export format (`buildMarkdownText`, `OutputPanel.tsx:94-114`): title as `#`, each
  section as `##`, each step as a numbered line with an indented quote and italic bridging
  explanation, a trailing `## Gap Report` section. Worth keeping as the reference Markdown
  rendering regardless of what generates the underlying `SopDocument`.
- The four output tones (`step-by-step`, `formal`, `wiki`, `bulleted`) as a concept, though how they
  are validated (currently: not at all, a bare string, `06-known-gaps.md`... actually not a gap
  entry but noted in `05-frontend-behavior.md` section 5) should tighten to an enum in the rebuild.

## Rewrite -- same purpose, different mechanism

- **Orchestration**: browser `useState`/`useRef` state machine -> server-side `Run`/
  `StageExecution` persistence and an orchestrator that can run unattended
  (`07-target-architecture.md`, `08-target-pipeline-spec.md`).
- **Response parsing**: the four-layer unwrap stack (`parseLLMJson` -> `normalizeResponse` ->
  `parseAgentResult` -> `extractText`, `04-data-contracts.md` section 2) -> provider-native
  structured output validated directly into pydantic models, one contract per stage.
- **Prompt management**: inline string literals and template functions inside `app/page.tsx`
  (`DEMO_PROMPTS`, the live prompt strings) -> versioned files under `prompts/`, referenced by
  `agents/roles.py`.
- **The judge loop**: manual "Run Another Pass" button (`06-known-gaps.md` entry 3) -> automatic
  orchestrator loop with an optional human-checkpoint mode (`08-target-pipeline-spec.md`, "Real
  loop semantics").
- **Demo/sample-data mode**: hardcoded output dump (`06-known-gaps.md` entry 2) -> recorded
  real-response fixtures (see "Preserve as fixtures" below) that can be replayed as an offline demo
  *and* used as regression test assertions -- the same data serves both purposes instead of the
  demo being disconnected from any test.
- **Visual gap filling**: text-only prose description -> real frame extraction and vision-model
  calls, per `09-visual-gap-filler.md`.

## Drop -- Lyzr Architect template scaffolding, not part of the product

Per the file classification in `02-current-architecture.md` section 2, everything marked TEMPLATE
there:

- `lib/ragKnowledgeBase.ts`, `lib/scheduler.ts` -- unused subsystems, never called by ProcessGhost.
- `lib/iframeLogger.ts`, `lib/agent-fetch-interceptor.ts`, `components/AgentInterceptorProvider.tsx`
  -- the Architect editor's live-debugging harness (console/error forwarding to a parent iframe,
  "Fix with AI" modal). Meaningless outside the Architect editor context.
- `components/KnowledgeBaseUpload.tsx`, `app/api/rag/route.ts`, `app/api/scheduler/route.ts` --
  unused API surface.
- The Architect badge script embedded in `app/layout.tsx:32` (a large inline `<Script>` block
  rendering a "Built with Architect" badge).
- Every `components/ui/*` file the app never actually renders (accordion, carousel, chart, command,
  sidebar, menubar, etc. -- `02-current-architecture.md` section 2 lists which subset is actually
  used: card, button, input, label, switch, select, tabs, badge, collapsible, scroll-area).
- The `lyzr-architect` npm package and everything in `CLAUDE.md:429-583` describing its database/
  auth helpers -- ProcessGhost has no database and never imports this package.
- `hooks/useAgent.ts` -- a generic hook never imported by `page.tsx`, which calls `callAIAgent`
  directly instead.
- Netlify/Docker/Next.js build config (`netlify.toml`, `Dockerfile`, `next.config.js`,
  `tailwind.config.ts`, etc.) -- irrelevant to a Python service.

None of this needs a Python equivalent; it was never part of ProcessGhost's actual behavior.

## Preserve verbatim as test fixtures

`app/demoData.ts` (514 lines) is worthless as a live demo (`06-known-gaps.md` entry 2) but is
genuinely valuable as data:

- `DEMO_TRANSCRIPT_TEXT` / `DEMO_TRANSCRIPT_RESULT` -- a known-good transcription example for the
  bills-tracker scenario described in `01-product-overview.md`.
- `DEMO_EXTRACTION_RESULT`, `DEMO_GAP_DETECTION_RESULT`, `DEMO_VISUAL_FILLER_RESULT` -- defined in
  the as-is code but never actually consumed by the running demo (`05-frontend-behavior.md` section
  6) -- promote these to actually-used intermediate fixtures in the Python test suite, since they
  are well-constructed and currently going to waste.
- `DEMO_SOP_RESULT` / `DEMO_JUDGE_RESULT` -- the reference for what a well-formed, fully-resolved
  SOP and an `APPROVED` verdict look like; useful both as a regression assertion target and as the
  "known good" example to show a first-time user of the rebuilt system.

Recommended fixture use: record a real run of the pipeline against a real recorded bills-tracker
walkthrough video once the target pipeline exists, and let *that* recording become the new demo
fixture (replayed without live API calls for a fast/free demo mode, but originating from and
periodically re-validated against a real pipeline execution) -- rather than hand-authoring output
the way `app/demoData.ts` was. The existing `DEMO_*` constants remain useful in the meantime as
placeholder fixtures and as the acceptance-test target for "does the target pipeline, given this
transcript, converge on materially this SOP."

## Field-mapping table: response_schemas -> pydantic models

| `response_schemas/*.json` | Target pydantic model | Notes |
|---|---|---|
| `transcription_agent_response.json` | `models.transcript.Transcript` (+ `TranscriptTurn`) | direct port |
| `process_extraction_agent_response.json` | `models.steps.StepList` (+ `Step`) | `prerequisites: list[str]`, `confidence: float` -- fixes the string-typed schema mismatch noted in `04-data-contracts.md` section 1.7 |
| `gap_detection_agent_response.json` | `models.gaps.GapList` (+ `Gap`) | `severity` as an enum (`low/medium/high/critical`, observed values) rather than a free string |
| `visual_gap_filler_agent_response.json` | `models.visual_fills.VisualFillList` (+ `VisualFill`) | `screen_actions`, `ui_elements_identified` as `list[str]`; `confidence: float`; `data_entered: str \| None` -- all fixing the same string-typed mismatch pattern; `frames_analyzed` now accurate per `09-visual-gap-filler.md` |
| `documentation_synthesis_agent_response.json` | `models.sop.SopDocument` (+ `SopSection`, `SopStep`, `GapReportEntry`, `SopMetadata`) | `annotation_type` as an enum matching the UI's known categories (`05-frontend-behavior.md` section 4: verbal / visual / judge / original) |
| `sop_judge_agent_response.json` | `models.judge.JudgeVerdict` (+ `JudgeEvaluation`, `JudgeFinding`) | `verdict` as a `Literal["APPROVED", "GAPS_REMAINING"]`, not a free string -- see `08-target-pipeline-spec.md` stage 6 |

`module_outputs.artifact_files` (`04-data-contracts.md` section 3, synthesis-agent-only file
output) maps to an optional `artifact_url: str | None` on `SopDocument` or on the `Run` itself,
depending on where the rebuilt synthesis stage produces its file output -- left open pending the
provider decision in `07-target-architecture.md` (a directly-called SDK may not have an equivalent
"artifact file" concept the way Lyzr's `module_outputs` does; this may become an explicit rendering
step instead, e.g. "render `SopDocument` to PDF" as its own utility rather than something the LLM
call itself produces).
