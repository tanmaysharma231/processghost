# ProcessGhost Documentation

## What ProcessGhost is

An expert records a screen walkthrough while narrating it, and skips almost everything a first-time
user would need -- the sheet name they navigated to, the exact column they pasted into, the
case-sensitive status value a formula depends on -- because those things are invisible to someone
who already knows them. ProcessGhost runs the walkthrough through a six-agent pipeline
(transcribe -> extract stated steps -> detect the gaps between what was said and what a newcomer
needs -> recover unverbalized screen actions from the video -> synthesize an annotated SOP -> have
a "judge" agent audit it and loop back if gaps remain) to produce a document a new hire can actually
follow. See `01-product-overview.md` for the full worked example baked into the repo.

## What exists today

`E:/pythonProject/agentic/Process_ghost/` holds four things:
- `process-ghost-incredible-node-qh4q-main/` (plus its source zip) -- the original Lyzr
  Architect-generated Next.js 14 hackathon build. The six-agent design is genuinely thought
  through, but the flagship demo makes zero live agent calls, the vision agent that is supposed to
  watch the screen recording never receives a single frame of video, and the judge loop the
  pipeline is named around requires a human to click a button between every iteration instead of
  running automatically. This document set (Parts 1-2) exists because none of that was written
  down anywhere before it.
- `docs/` -- this document set.
- **`processghost/`** -- the actual Python implementation. No longer just under development --
  it has been run end to end against real files (see `data/` below) with real Whisper transcription
  and real vision calls, and it works. It is *not* the from-scratch rebuild Part 3 below originally
  specced; see "Implementation status" next.
- **`data/`** -- a real narrated walkthrough video (`english_hackathon_test.mp4`) and its
  companion workbook (`ProcessGhost_Bills_Tracker_v4_final.xlsx`), matching the scenario
  `01-product-overview.md` described before any real file existed. See `12-real-scenario-target.md`
  for the grounded, verified version of that scenario -- what the video actually says, what the
  workbook actually contains, and exactly what gaps the pipeline's output needs to surface.

## Implementation status (read this before Part 3)

Part 3 (`07`-`11`) was written before a second, separate hackathon project of the same user's --
`AI_ENGINEER_DAY_HACKATHON/process-integrity/` -- was known to exist. That project already had
real, working infrastructure for most of this problem: ffmpeg frame extraction, Whisper
transcription, an OpenAI client wrapper, Pydantic models, a deterministic gap-detection rule
engine, report rendering, run persistence, and a Streamlit UI. Its intent-extraction and
action-description stages were stubbed with heuristics rather than real LLM calls.

**`processghost/` was seeded from that project, not built from scratch against Part 3's design.**
Concretely, this means Part 3 is superseded on two points:
- **Provider: OpenAI-only** (Whisper + gpt-4o + gpt-4o-mini), not the multi-provider
  (Gemini/Claude/GPT) design `07-target-architecture.md` recommends. This matches
  process-integrity's existing, working `core/llm/client.py` exactly.
- **UI: Streamlit**, not Next.js-as-thin-client, not CLI-first -- process-integrity already had a
  working Streamlit app, answering `11-open-questions.md` #4.

Everything else in Part 3 (the run/audit-trail model, per-stage failure handling, the SOP
synthesis and judge-loop designs, the visual-gap-filler design) remains the intended direction --
it just isn't all built yet. See `processghost/logs/change-log.md` for what has actually been
implemented so far and `processghost/README.md` for what's real vs. not yet built.

## Decisions behind how this doc set is organized

Recorded here because they shape every document below and were made explicitly, not assumed:

1. **As-is and target-state are documented separately and never mixed.** Part 1 states facts about
   the existing code with no opinions; Part 3 states a design with no false history. Part 2 (the
   gap register) is the bridge between them.
2. **Docs live outside the extracted app**, at `Process_ghost/docs/`, specifically so re-extracting
   the zip cannot overwrite or get confused with this documentation.
3. **The target is a full Python rebuild.** The Next.js app is treated as a spec and reference
   implementation to port concepts and contracts from -- not as the codebase that gets evolved in
   place. What (if anything) the future UI looks like is an open question (`11-open-questions.md`),
   not settled here.
4. **Real video frame extraction is in scope**, and is treated as the #1 priority gap. The current
   "Visual Gap Filler" agent reasons over a paragraph of prose a developer wrote describing a
   spreadsheet from memory -- it has never seen a pixel. Fixing this is the subject of its own
   document, `09-visual-gap-filler.md`.
5. **Demo mode is documented as a correctness risk, not a feature.** "Try with Sample Data" plays
   back 514 lines of hardcoded output with zero API calls and cannot reveal whether the real
   pipeline works at all. The app's own onboarding copy admits this. It is preserved only as
   fixture material for the rebuild, not carried forward as a UX pattern uncritically.

## Reading order and index

If you only read one document, read **`06-known-gaps.md`** -- it is the ranked list of everything
wrong with the as-is system and whether the rebuild must fix it.

### Part 1 -- As-is (reverse engineering)

| Doc | Hook |
|---|---|
| `01-product-overview.md` | The expertise-blindness problem and the bills-tracker example baked into the repo -- the clearest statement of intent in the whole codebase, and it's buried in a constant. |
| `02-current-architecture.md` | Full file tree classified app-specific vs. unused template scaffolding; runtime topology; the exact request lifecycle for one agent call, upload to poll to response. |
| `03-agent-pipeline.md` | The six agents, their declared graph, and what the code actually executes -- including every prompt template verbatim and why the "loop" is a button. |
| `04-data-contracts.md` | Every response schema field, plus the four independent layers of JSON-unwrapping a response survives before a React component reads it. |
| `05-frontend-behavior.md` | The UI's full state machine, every section, and the two demo-mode code paths -- only one of which is reachable, and it calls no agent. |

### Part 2 -- The gap register

| Doc | Hook |
|---|---|
| `06-known-gaps.md` | Twelve findings, ranked by severity, each with evidence and a fix-required verdict. Start here. |

### Part 3 -- Target state (Python rebuild)

| Doc | Hook |
|---|---|
| `07-target-architecture.md` | Server-side durable runs replacing browser state; the provider decision (direct SDK calls, recommended, over staying on Lyzr); pydantic replacing the four-layer unwrap stack. **Provider choice superseded -- see "Implementation status" above: `processghost/` is OpenAI-only.** |
| `08-target-pipeline-spec.md` | Per-stage contracts, the judge loop actually running automatically, and replacing silent truncation with visible context budgeting. |
| `09-visual-gap-filler.md` | The design that makes the product's central claim real: ffmpeg frame extraction at gap timestamps, scene-change sampling, real crop, sent to a vision model. |
| `10-migration-map.md` | Port / rewrite / drop / preserve-as-fixture, decision by decision, plus the full response-schema-to-pydantic field mapping. |
| `11-open-questions.md` | What's still undecided: execution model, persistence backend, UI layer, transcription provider. Question #6 (sourcing a real test video) is now resolved -- see `data/` and `12-real-scenario-target.md`. |

### Part 4 -- Real scenario, verified

| Doc | Hook |
|---|---|
| `12-real-scenario-target.md` | The actual acceptance target, grounded in the real files at `data/` and a real verified pipeline run: exactly which steps the narration states, which six things it never says (and why each one matters, using the workbook's real formulas and real garbled transaction strings), and what the SOP-synthesis stage's output must contain to be considered correct. Supersedes `01-product-overview.md`'s placeholder example as the working test case. |

## A note on citations

Every as-is claim across `01`-`06` cites `file:line` against
`process-ghost-incredible-node-qh4q-main/`. If code in that folder changes, those citations may
drift -- re-verify against the source before trusting a specific line number for anything
load-bearing.
