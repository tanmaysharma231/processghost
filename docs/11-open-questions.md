# 11 - Open Questions

Decisions the rebuild still needs, each with options and a recommendation. Nothing here is settled
by this document alone -- these are inputs to a decision, not the decision.

## 1. Lyzr-hosted agents vs. direct provider SDKs

Already addressed as a stated recommendation in `07-target-architecture.md` ("Provider decision"):
**direct SDK calls (Anthropic / Google / OpenAI), recommended.** Repeated here because it is the
architectural decision every other document downstream assumes. If reversed later, `08` and `09`'s
structured-output and vision-call designs need re-checking against whatever Lyzr's hosted-agent
interface actually supports for those same needs (does it expose structured output enforcement? can
images be attached to a hosted-agent call the same way?).

## 2. Synchronous vs. queued execution

- **Synchronous** (a run executes inline within the request/process that started it, blocking until
  done or until it's checkpointed at a judge-loop pause). Simple, matches the as-is app's mental
  model most closely, easiest to build first.
- **Queued** (a run is submitted, a worker picks it up, the caller polls or gets a callback/webhook
  -- structurally similar to what Lyzr's own async task API already does,
  `02-current-architecture.md` section 4). Necessary for anything longer-running than a single
  request/response cycle can comfortably hold, and necessary for the "batch-process a folder of
  walkthroughs unattended" goal named in `06-known-gaps.md` entry 8's fix rationale.

**No recommendation yet** -- depends on expected run volume and whether a single-process CLI tool
is the first target (in which case synchronous is fine to start) or a service with concurrent users
(in which case queued is needed sooner). Revisit once `11.4` (UI layer) is decided, since a CLI-first
build can defer this indefinitely.

## 3. Run persistence backend

- **SQLite** -- zero-ops, file-based, sufficient for single-machine use, easy to inspect directly.
- **Flat JSON files per run** -- simplest possible implementation of the `Run`/`StageExecution`
  model from `07-target-architecture.md`, trivially matches the "one run = one record" mental model,
  but loses queryability across runs (e.g. "show me all failed runs this week") without extra
  tooling.
- **Postgres** -- appropriate if this becomes a real multi-user service; overkill for anything
  earlier than that.

**Recommendation: start with SQLite.** It gets queryability across runs for free (unlike flat
files) without introducing an external service dependency, and migrating to Postgres later is a
low-cost move if the schema is kept simple (it should be, per the `Run`/`StageExecution` model
already designed).

## 4. UI layer

- **Keep the Next.js app as a thin client** over the new Python backend -- reuses the UI design
  work documented in `05-frontend-behavior.md` (the Debug Panel concept especially, and the
  Clean-SOP/Gap-Report tabbed output view), at the cost of maintaining two languages/runtimes.
- **Replace with a new UI** (framework unspecified) purpose-built against the new `Run`/
  `StageExecution` API shape.
- **CLI first** -- given the parent directory is `pythonProject/agentic/` and the rebuild target is
  Python end to end, a CLI (`processghost run <video>`, `processghost show <run_id>`) may be the
  fastest way to get the corrected pipeline (`07`, `08`, `09`) working and testable before investing
  in any UI at all.

**No recommendation yet, but noted:** CLI-first is attractive specifically because it lets
`08-target-pipeline-spec.md` and `09-visual-gap-filler.md` be built and validated in isolation from
any UI decision, and the Debug Panel's information (per-stage prompt/response/timing) maps directly
onto CLI output (`processghost show <run_id> --stage 4`) without needing a UI at all to be useful.
Revisit after the pipeline itself works end to end against a real video.

## 5. Audio transcription: hosted multimodal model vs. local Whisper

- **Hosted multimodal model** (as-is: Gemini 2.5 Pro handles both audio understanding and
  transcription in one call, `workflow_state.json:6-15`) -- simplest, matches the as-is agent
  design, handles code-switching (Hinglish, per `01-product-overview.md`'s worked example) well
  given Gemini's multilingual training.
- **Local Whisper** (or a Whisper-derivative) for the transcription step specifically, keeping only
  the reasoning-heavy stages (extraction, gap detection, synthesis, judge) on hosted LLM APIs --
  removes per-run transcription API cost and a network dependency for that one step, at the cost of
  local compute/GPU requirements and needing separate handling for speaker labeling (which Whisper
  alone does not do) and code-switching quality (worth validating against the Hinglish example
  before committing).

**No recommendation yet** -- needs a real accuracy comparison against actual code-switched
walkthrough audio (the kind `01-product-overview.md`'s scenario describes) before choosing; this is
an empirical question, not an architectural one.

## 6. Sourcing real test walkthrough videos

The entire as-is fixture set (`app/demoData.ts`) is synthetic -- there is no real recorded video
anywhere in the repository, and the "video" `loadDemoData()` creates is a fake `Blob`
(`05-frontend-behavior.md` section 6). The Python rebuild's fixture strategy
(`10-migration-map.md`, "Preserve verbatim as test fixtures") depends on at least one **real**
recorded screen-walkthrough-with-narration video to validate the pipeline end to end, especially
stage 4 (`09-visual-gap-filler.md`), which cannot be meaningfully tested against synthetic text
descriptions at all -- it needs actual pixels with actual scene changes to prove frame extraction
and scene-change sampling work.

**Action needed, not a design decision:** record (or otherwise obtain) at least one real short
walkthrough video, ideally recreating the bills-tracker scenario from `01-product-overview.md` so
the existing `DEMO_SOP_RESULT`/`DEMO_JUDGE_RESULT` fixtures remain usable as an acceptance-test
target for the real pipeline's output.
