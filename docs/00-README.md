# ProcessGhost Documentation

## What ProcessGhost is

An expert records a screen walkthrough while narrating it, and skips almost everything a first-time
user would need -- the sheet name they navigated to, the exact column they pasted into, the
case-sensitive status value a formula depends on -- because those things are invisible to someone
who already knows them. **The shipped Python implementation (`processghost/`) turns that recording
into an annotated SOP a new hire can actually follow, through a Writer -> Reviewer -> Checker ->
Triage chain** -- see `14-writer-reviewer-checker.md` for the design and `12-real-scenario-target.md`
for the worked example it's built and verified against.

## What exists today

`E:/pythonProject/agentic/Process_ghost/` holds:
- `docs/` -- this document set.
- **`processghost/`** -- the actual Python implementation. No longer just under development --
  it has been run end to end against real files (see `data/` below) with real Whisper transcription
  and real vision calls, and it works.
- **`data/`** -- a real narrated walkthrough video (`english_hackathon_test.mp4`) and its
  companion workbook (`ProcessGhost_Bills_Tracker_v4_final.xlsx`). See
  `12-real-scenario-target.md` for the grounded, verified scenario -- what the video actually says,
  what the workbook actually contains, and exactly what gaps the pipeline's output needs to surface.

## Decisions behind how this doc set is organized

Recorded here because they shape every document below and were made explicitly, not assumed:

1. **Docs live outside the implementation**, at `Process_ghost/docs/`, so they survive independent
   of any single code layout.
2. **Real video frame extraction is in scope** and load-bearing: the pipeline's Checker stage
   reasons over actual frames pulled from the recording, not prose descriptions written from memory.
3. **Demo/sample-data shortcuts are a correctness risk, not a feature.** Anything that plays back
   canned output with zero API calls cannot reveal whether the real pipeline works.

## Reading order and index

### Real scenario, verified

| Doc | Hook |
|---|---|
| `12-real-scenario-target.md` | The acceptance target, grounded in the real files at `data/` and a real verified pipeline run: exactly which steps the narration states, which six things it never says (and why each one matters, using the workbook's real formulas and real garbled transaction strings), and what the SOP-synthesis stage's output must contain to be considered correct. |
| `13-current-logic-and-architecture.md` | Full snapshot of the v1 two-parallel-token-stream architecture (intent/action tokens, keyword+time aligner, six-rule dissonance engine) and the OpenAI->Sarvam provider migration. **Describes an architecture superseded by the section below, and since fully removed** -- kept as the as-is reference `14` explains the departure from. `core/align/`/`core/rules/` were deleted once the Checker (Phase 3) replaced them end-to-end; they no longer exist on disk. |

### v2: Writer / Reviewer / Checker / Triage (**complete** as of 2026-08-29)

| Doc | Hook |
|---|---|
| `14-writer-reviewer-checker.md` | Why the v1 aligner was replaced, the three-agent chain (Writer drafts naively, Reviewer flags gaps, Checker resolves them against frames), what's kept vs. removed from v1 and on what schedule, and the module-layout decisions (`core/models/sop.py` as a new file, not an in-place `tokens.py` edit). |
| `15-provenance-and-doc-format.md` | The attribution grammar (`[said @t]` / `[inferred from screen @t]` / flag references) that is the actual core of the v2 design, how it's mechanically enforced (a raising Pydantic validator, not a soft flag), the four-section `sop.md` layout with a real rendered example, the degraded-STT timing escape hatch, and why rendering is deterministic rather than LLM-authored. |
| `16-prompt-guide.md` | Prompt-writing lessons for this repo's three LLM stages -- checked against Anthropic's and OpenAI's own prompting guides directly, plus this project's own hard-won mechanisms (quote-grounded coverage -- not omission alone, which proved unstable --, force-observation-before-conclusion, explicit permission to answer "I don't know"). Read before rewriting any prompt in `core/llm/prompts.py`. |
| `18-known-gaps-v2.md` | The defect register for the current v2 pipeline. Currently: the Reviewer's structural-flag count is still unstable across real runs (quote-grounding fixed fabricated quotes counting as coverage, but not the model sometimes skipping the transitions task) -- the reliability warning now correctly flags exactly those runs instead of presenting them with false confidence; `trace.jsonl` now carries real call payloads, not just cost/latency; the Checker hit real OpenAI rate limits in every acceptance run; `MAX_CHECKED_FLAGS` has never once bound and is being left alone. |

### Reference

| Doc | Hook |
|---|---|
| `21-pipeline-reference.md` | The whole run in one place: each stage's job, inputs, outputs, failure behavior and prompt (copied verbatim in an appendix), plus triage rules and the files a run produces. |
| `22-architecture-v3.md` | v3 design: independent screen timeline (built), Align for unspoken steps and a cheaper change-driven Checker (not built), 20 decisions with rejected alternatives, data contracts, measured worked example, roadmap with exit checks. |
| `23-checker-learnings.md` | What the v2 Checker was sent and said, measured by replaying five real runs: two thirds of sampled frames carried no change, a sheet switch is only ~10.8% of pixels, and the model often echoes the prompt. Evidence behind the v3 design. |

### Deep dives

| Doc | Hook |
|---|---|
| `24-speech-to-diff-deep-dive.md` | Word-level speech timing against what changed on screen, event by event, on the real clip: which of the six required findings the diffs already contain, where narration lags the action, and the reconciler design this points to. |

### Decisions and limits

| Doc | Hook |
|---|---|
| `data-handling.md` | What a run stores on disk, what is sent to the model provider, what is NOT protected (no redaction, no encryption at rest), and the controls that exist: `--no-keep-media`, `--no-cache`, `scripts.cleanup`. |
| `scope-and-limits.md` | What ProcessGhost deliberately does not do, and the known gaps (one clip, no web screens, Reviewer variance, weak resolved answers). |
| `adr/` | Nine short decision records: what was chosen, why, what was rejected, and whether it is built, planned or open. |

### Fit notes

| Doc | Hook |
|---|---|
| `20-lab0-fit.md` | How ProcessGhost could feed a discovery workflow (Lab0), why it is built the way it is, and an honest built/partial/planned status for every claim. Working notes, not a pitch. |

All four v2 phases (Writer, Reviewer, Checker, Triage/`build_spec.json`) are built and verified
against the real video -- see `../processghost-v2-writer-reviewer-checker-plan.md`'s status banner
for the original design brief and known deviations from it.
