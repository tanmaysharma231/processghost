# ProcessGhost v2 Implementation Plan — Writer / Reviewer / Checker (for Claude Code)

> **STATUS (2026-08-30): Phases 1-4 below are complete and verified against the real video.**
> Phase 5 (long-video stress test) has not been run -- no long video exists yet and
> `docs/17-long-video-findings.md` (named at Phase 5 below) has not been created. This file is the
> original design brief, kept because real code docstrings cite it by this exact path
> (`core/llm/client.py`, `core/llm/prompts.py`, `core/models/sop.py`, `core/pipeline.py`,
> `docs/14-writer-reviewer-checker.md`) -- do not delete or rename it without updating those.
> For as-built reality, read `docs/13-current-logic-and-architecture.md` through
> `docs/18-known-gaps-v2.md` instead of this file. Known deviations from what's written below:
> - The OpenAI path uses LangChain's `with_structured_output`, not the raw SDK.
> - There is no `core/sop/` package (flat functions in `core/pipeline.py` instead).
> - A resolved structural flag inserts a new `SOPStep` rather than only adding a clause to an
>   existing one.
> - **The Reviewer's gap-detection went through two mechanisms, not one.** First built as
>   coverage-by-omission alone (line 24 below): a transition counts as coverage whenever the model
>   names one, full stop. Measured as unstable in practice -- 0 to 5 structural flags across 18
>   runs of the *same* video (`docs/18-known-gaps-v2.md` entry 1) -- because silence from a
>   genuinely unbridgeable pair and silence from a model that simply didn't do the work are
>   indistinguishable as an omitted row. Fixed by requiring every transition to also carry a
>   `quote` (a verbatim transcript span), verified in code (`_quote_is_grounded`,
>   `core/llm/parsing.py`) as an actual substring of the transcript before the pair counts as
>   covered; an invented, paraphrased, or missing quote is now treated exactly like an omission.
>   Real-run confirmation that this stabilized the flag count is still owed (`docs/18` entry 1).
> - **`sop.md`'s layout (line 148-149 below, and the augmented-text example at line 117-119) is
>   superseded.** Flags are no longer rendered as inline callout blocks under each step. The
>   current renderer produces four sections in fixed order: the workflow as spoken (said-only
>   narration + the mermaid flowchart), what the pipeline assumed (inferred clauses and
>   Checker-inserted steps), what is missing grouped by why (contradicted / unresolvable / not
>   checked, plus a warning when the Reviewer's two internal checks disagree), and a flat,
>   severity-ordered question list. See `docs/15-provenance-and-doc-format.md`.
> - **`CHECKER_SYSTEM` (line 101-124 below) is XML-sectioned**
>   (`<role>`/`<context>`/`<scope>`/`<procedure>`/`<verdicts>`/`<output_format>`), matching
>   `WRITER_SYSTEM`/`REVIEWER_SYSTEM`'s structure, and gained a `<scope>` section this brief never
>   specified: every specific name the model writes must be legible in the evidence given, not
>   merely plausible. `<context>` also reframes `unresolvable` (line 123 below: "flag stays open --
>   these become the escalation queue") as a genuine, useful outcome -- a question worth taking
>   back to the person who recorded the walkthrough -- rather than a dead end.
> - The new-docs list at the very bottom of this file (`16-build-spec-and-triage.md`) doesn't match
>   what actually shipped as `16`: `docs/16-prompt-guide.md`. The defect register mentioned nowhere
>   in this brief shipped as `docs/18-known-gaps-v2.md` (originally `17`, renumbered to keep `17`
>   free for this file's own Phase 5 long-video findings doc, named just above).

## Context

ProcessGhost takes a narrated screen recording of a business process and produces documentation
that surfaces the gap between what the expert SAID and what is actually needed to perform the
process. Existing repo: `processghost/` (Python). Read `docs/13-current-logic-and-architecture.md`
first for the current pipeline and repo layout.

**This plan replaces the current architecture** (parallel intent/action token streams + keyword
aligner + cross-check diff) with a document-centric agent chain designed by the project owner:

> **Writer** drafts the SOP from the transcript → **Reviewer** flags ambiguities and suspected
> gaps inline → **Checker** resolves each flag by examining video frames in a context-bounded
> time window. The SOP markdown document is the spine: every step, flag, evidence, and
> resolution lives in/on it, and every line is timestamp-traced.

### The provenance rule (non-negotiable, the core of the design)

Every content line in the generated doc carries exactly one attribution:

- `[said @t]` — stated in narration at time t
- `[inferred from screen @t]` — resolved by the Checker from frames, visibly tagged, never
  silently promoted to fact
- `[FLAG-nn: <question>, window t1–t2, status: open|resolved|unresolvable]`

Nothing unattributed. This gives auditability (trace any step to its source), escalation quality
(every open flag ships with its evidence window), and future eval ground truth (flag + window +
eventual human answer = labeled data).

### What is kept from the current pipeline

- STT (Whisper / Sarvam Saaras batch) producing timestamped `TranscriptSegment`s
- Frame extraction (ffmpeg) and the per-frame vision (OpenAI) / OCR-then-reason (Sarvam) calls
- Provider abstraction (`.chat()` interface, `config.LLM_PROVIDER` switch), Metrics,
  degrade-loudly failure handling, Pydantic data contract style, scenario-agnostic prompts
- The `named_specifically` test ("could a stranger find this without guessing") — reused as the
  Reviewer's ambiguity criterion
- The grounding scenario and acceptance target (`docs/12-real-scenario-target.md` section 3)

### What is removed

- `core/align/` (keyword+time aligner) and the TAU thresholds
- The parallel IntentToken/ActionToken streams as the primary structure (ActionToken survives
  only as the Checker's frame-description shape)
- The six-rule dissonance engine (`core/rules/`) — the Reviewer + Checker subsume it. Keep the
  files until Phase 3 acceptance passes, then delete.
- The old cross-check call (`CROSS_CHECK_SYSTEM`) — subsumed by the Reviewer.

---

## The three agents

### Writer — transcript → draft SOP (one LLM call)

**Input:** all transcript segments, indexed with timestamps.
**Output:** `sop_draft.md` + parallel `SOPDocument` model.

- Convert narration into ordered imperative steps. Every step: title, instruction, `[said @t]`
  attribution to the segment(s) it came from.
- Write ONLY what was said. No filling, no inference, no cleanup of vague references — "paste it
  here" stays "paste it here". The Writer is deliberately naive; finding problems is the
  Reviewer's job, and a Writer that quietly repairs gaps destroys the signal.
- Non-actionable segments (small talk) are omitted but listed in a doc appendix with timestamps
  so nothing is silently dropped.

### Reviewer — draft SOP → flagged SOP (one LLM call)

**Input:** the full draft + full transcript (for context the Writer may have compressed).
**Output:** same doc with inline `[FLAG-nn]` annotations + a flag registry (model: `Flag`).

Reads as a newcomer with zero process knowledge. Two flag families:

1. **Ambiguity flags** — a reference fails the "could a stranger find this without guessing"
   test: unnamed sheets/files/columns ("here", "this one", "monthly actions"), missing selection
   logic (an item is picked from among similar ones with no stated rule), unverified claims
   (anything asserted as automatic/consequential).
2. **Structural gap flags** — suspected missing steps or logic: jumps where the state before
   step N+1 couldn't result from step N alone, outcomes that appear without a producing step,
   prerequisite-shaped absences (nothing was opened/located before an edit). The Reviewer may
   flag on suspicion — false positives are acceptable; the Checker adjudicates.

Each flag carries: a question phrased for a human ("Which column does the copied value go
into?"), severity (`blocking | caution | note` — would a newcomer be stopped, slowed, or merely
annoyed), and a **context window**: the transcript segment range where this topic is being
discussed/shown, from where it starts to where the narration moves on. Segment range, not fixed
seconds — the Checker converts it to a time range. This window choice is part of the Reviewer's
job and one of its two hardest judgments (the other is structural-gap suspicion).

### Checker — per flag: frames → resolution (one LLM call per flag)

**Input per flag:** the flag (question + window), the relevant doc excerpt, and frames extracted
across the flag's time window (window start minus a small lead-in, config
`CHECKER_LEAD_IN_S = 3.0`, dense interval `CHECKER_FRAME_INTERVAL_S = 0.5`, per-flag frame cap).
**Output:** a `Resolution` per flag: `resolved | contradicted | unresolvable`, with evidence
frame paths and timestamps.

- **One call per flag is deliberate** — an explicit exception to the repo's batch-everything
  principle. Each flag has its own window, frames, and question; batching unrelated checks
  muddies each. Document this exception in the code and docs. Cost control comes from a run-level
  cap (`MAX_CHECKED_FLAGS`, default 20, processed in severity order: blocking first), overlapping
  windows sharing extracted frames, and frame dedupe.
- Sarvam path: frames go through ONE batched document-intelligence OCR job for all checked flags
  (job latency makes per-flag jobs unacceptable), then each flag's call reasons over its own
  frames' OCR text. OpenAI path: true vision per call.
- On `resolved`: the resolution is written INTO the step as `[inferred from screen @t]` — the
  vague text is augmented, not replaced ("paste it here `[said @0:12]` — Column F 'Actual'
  `[inferred from screen @0:14]`"). Flag status → resolved, but the flag stays in the registry
  (audit trail).
- On `contradicted` (screen shows something incompatible with narration): severity forced to
  blocking, both versions recorded.
- On `unresolvable`: flag stays open — these become the escalation queue.
- Unchecked flags (over cap) are marked `unchecked`, never silently dropped.

---

## Pipeline (new `core/pipeline.py` flow)

1. Setup, audio extraction, STT — unchanged.
2. Frame extraction: keep a sparse uniform pass (interval can widen to 4–5s; it now only serves
   Checker lead-ins and the appendix) — dense extraction happens per-flag on demand.
3. Writer call → `SOPDocument` + `sop_draft.md`.
4. Reviewer call → flags merged into doc + `Flag` registry.
5. Checker loop → `Resolution`s merged into doc, flag statuses updated.
6. Render outputs (below) + persist everything (doc, models JSON, metrics).

Failure handling, same philosophy as before, adapted:
- Writer fails → run aborts with a recorded failure (no doc = nothing downstream; this is the
  one fatal stage).
- Reviewer fails → doc ships with zero flags + a prominent "REVIEW FAILED" banner in the doc and
  a Metrics failure. Never ship an unflagged doc that looks reviewed.
- A single Checker call fails → that flag stays open with status `check_failed`, run continues.
- Whole OCR batch fails (Sarvam) → all flags stay open as `check_failed`.

## Outputs (two renderings, one source)

1. **`sop.md`** (human): the final doc — steps with inline attributions, resolved flags shown as
   augmented text, open flags as visible callout blocks with their questions and windows.
2. **`build_spec.json`** (future build agent): same steps + flag registry, plus per-flag triage:
   `proceed_with_assumption` (resolved via inference), `proceed_with_flag` (caution/note, open),
   `block_and_escalate` (blocking, open or contradicted). Triage is a small deterministic
   function over (status, severity) — auditable, replaceable later. Open blocking flags form the
   `escalation_queue`, each with question + evidence window + step id, so an answer can be
   patched back to the exact step.

## Data contract (rewrite `core/models/tokens.py`)

- `TranscriptSegment` — unchanged.
- `Attribution` (kind: `said | inferred`, timestamp, segment ids / frame paths)
- `SOPStep` (id, order, title, instruction, attributions, flag_ids)
- `Flag` (id, family: `ambiguity | structural`, question, severity, window_segment_ids,
  window_time_range, step_id, status: `open | resolved | contradicted | unresolvable |
  unchecked | check_failed`)
- `Resolution` (flag_id, verdict, evidence frames + timestamps, inferred_text)
- `FrameDescription` (slimmed ActionToken: frame_path, timestamp, description, app, confidence)
- `SOPDocument` (run_id, steps, flags, resolutions, appendix_segments, provenance_complete:
  bool — validator asserts every step has ≥1 attribution)
- `RunArtifacts` (run_id, paths, transcript, sop, metrics, report/spec paths)

A validator on `SOPDocument` enforces the provenance rule mechanically: no step without
attribution, no flag without a window, no resolution without evidence. Fail loud in tests.

## Prompts (rewrite `core/llm/prompts.py`)

`WRITER_SYSTEM`, `REVIEWER_SYSTEM`, `CHECKER_SYSTEM` (+ `CHECKER_FROM_OCR_SYSTEM` for the Sarvam
text-only variant with lower calibrated confidence, mirroring the old
`ACTION_INFERENCE_FROM_TEXT_SYSTEM` pattern). All scenario-agnostic — no sheet/vendor/column
names anywhere; the real video remains a genuine test, not a template. JSON output shapes match
the models field-for-field.

---

## Phases (each independently shippable)

### Phase 1 — Writer + provenance skeleton
Models, `WRITER_SYSTEM`, pipeline step, md renderer with `[said @t]` attributions, provenance
validator. Test on the real video: draft must contain the 7 narrated actions from
`docs/13` section 2, each attributed, and must NOT contain Column F / Bill ID / "Paid" (that
knowledge isn't in the narration — if it appears, the Writer is hallucinating repairs).

### Phase 2 — Reviewer
`REVIEWER_SYSTEM`, flag registry, inline flag rendering. Acceptance on the real video, without
scenario hints: flags must cover (a) the three vague sheet references, (b) the missing
row-matching/selection logic, (c) the unspecified paste destination, (d) the "updates
automatically" claim. Structural-gap family must be exercised (b is one). Log misses; iterate
prompt until 4/4 gap classes flagged.

### Phase 3 — Checker
Per-flag dense frame extraction, `CHECKER_SYSTEM` + OCR variant, resolution merge-back,
`[inferred from screen @t]` rendering. Acceptance: paste-destination flag resolves to Column F
from frames; the row-matching flag comes back `unresolvable` (nothing on screen shows the join
key) and lands in the escalation queue; the "automatic" claim must NOT resolve (no Status edit
was ever shown). After this passes, delete `core/align/` and `core/rules/`.

### Phase 4 — build_spec.json + triage + escalation queue
As specified in Outputs above. Acceptance: row-matching gap is the queue's top blocking item.

### Phase 5 — Long-video stress test (before any UI)
Record/obtain one 20–30 min realistic narrated process video. Expected breakpoints to
investigate and fix: STT batching, Writer/Reviewer context length (chunking strategy with a
rolling doc if needed), flag volume vs `MAX_CHECKED_FLAGS`, Checker window quality when
narration and screen drift apart. Findings go in a new `docs/17-long-video-findings.md` and
drive the next plan revision. No UI work before this phase.

## Cross-cutting rules

- Both providers must work for every stage; construction branches only in `_make_client`.
- Every LLM call site: try → `Metrics.record_failure(stage, meta)` → defined degradation above.
- No scenario specifics in any prompt; scenario facts live only in tests.
- New docs as you go: `14-writer-reviewer-checker.md`, `15-provenance-and-doc-format.md`,
  `16-build-spec-and-triage.md`, each in the existing docs' style, and update `00-README`.
- Suggested sessions: one per phase; end every session by running the full pipeline on the real
  video and diffing `sop.md` against the previous run.
