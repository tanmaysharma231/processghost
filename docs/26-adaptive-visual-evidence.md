# ProcessGhost — Adaptive Visual Evidence Architecture

**Status:** Proposed design / decision record, not an implemented or validated system  
**Scope:** Cost-efficient video evidence selection and transcript–screen alignment for workflow-discovery QA  
**Initial target:** 24–60-second spreadsheet / enterprise application demonstrations; later, longer SME discovery recordings  
**Design intent:** Retain meaningful silent actions while reducing expensive vision-model calls and avoiding unsupported workflow claims.

---

# Part I — Implementation prompt for a coding agent

> Copy this section into a development conversation. Inspect the existing ProcessGhost repository before changing anything; do not assume its pipeline, interfaces, or models match this document.

## Role and objective

You are working on **ProcessGhost**, a workflow-discovery QA system that compares SME narration with screen evidence to find omitted actions, unexplained assumptions, ambiguous references, possible contradictions, and unsupported assertions. The existing project has been described as a **Writer → Reviewer → Checker** pipeline; verify the actual code before integrating.

Design and implement an **adaptive visual evidence retrieval layer**, not a brute-force vision pass over video frames. Its job is to make a recorded screen **searchable evidence** that the reasoning pipeline can investigate on demand. The objective is **high recall of potentially meaningful screen events per unit of vision-inference cost**, while keeping evidence traceable and uncertainty explicit.

### Hard constraints

1. **Do not feed all sampled frames to an LLM.** Local video decoding and image differencing are comparatively cheap; multimodal inference is the costly part. Measure actual token/API costs rather than presuming savings.
2. Use **three independent investigation triggers**: (a) screen-context transitions, (b) transcript-related uncertainty, and (c) unexplained visual activity. Never rely only on narration; silent actions are a core test case.
3. **Merge overlapping triggers** into shared evidence episodes to avoid redundant investigations.
4. Perform cheap, high-recall visual candidate detection **before** semantic prioritization; large pixel changes are not necessarily significant, and tiny edits may matter greatly.
5. Support **progressive, bounded investigation**. An agent can request frames, crops, before/after comparisons, or longer intervals only when needed; define and enforce a configurable inspection budget.
6. Separate **observed facts**, **inferences**, and **unresolved hypotheses** in all intermediate data and findings. A screenshot can establish a visible value change, but often cannot prove whether it was typed, pasted, automatically updated, or authorized.
7. Preserve original timestamps, screen coordinates, transcript spans, and source provenance. Avoid generating assertions with unsupported specificity.
8. Prefer deterministic local processing for frame indexing, visual diffs, episode grouping, and caching. **Do not add a new decision-model service or more agents by default**; first test whether the existing reasoning agent can use a compact event index and visual tools.
9. Implement against **the existing repository's design** with minimal disruption. Provide interfaces, tests, and measurements before substantial refactoring.
10. Treat all parameters below as **tunable starting guesses**, not validated defaults.

## Proposed processing flow

### Phase 0 — Inspect the existing implementation

Document existing transcript formats, timestamps, frame extraction logic, vision model use, event/data schemas, Writer/Reviewer/Checker responsibilities, logging and pricing, and test harness. Identify which parts can be reused. Do not duplicate existing functionality.

### Phase 1 — Cheap evidence indexing, no per-frame vision calls

- Decode video locally at a modest **analysis cadence** (experiment with 2–4 FPS initially). These frames are for **local comparisons**, not all sent to the vision model. Retain ability to sample higher frequency around a suspected event.
- Compute visual changes using low-cost methods (e.g., normalized frame differences, structural similarity, region-level differences, temporal stability). Make sensitivity to small localized changes tunable. Do not rank events purely by changed-pixel area.
- Record timestamps, changed-region bounding boxes, representative pre/post frames, screen stability, and likely transition boundaries.
- Group nearby diffs into **activity episodes**, such as a scroll, worksheet switch, dialog interaction, or edit sequence. At this stage, these are candidate event boundaries, **not confirmed action labels**.
- Generate a compact, inspectable event index with enough provenance to retrieve raw frames later. Cache video frames and repeated queries.
- Analyze the timestamped transcript separately for described actions, references ("here," "that one"), uncertain rules, and claims worth visual verification.

### Phase 2 — Three independent triggers

**Trigger A: Screen-context transition.** Major visual/layout changes suggest a new worksheet, app, window, dialog, or task context. Request one sufficiently legible **full-context frame** after the transition, optionally a preceding frame. The goal is to establish a **working interface map**, not declare every item important: identifiable app/sheet, visible entities/columns/fields, relevant controls, and possible relationships. Distinguish observations ("Bill ID column visible") from hypotheses ("Bill ID is the matching key"). Reuse this map until meaningful context changes.

**Trigger B: Transcript uncertainty.** Spoken references, incomplete rules, or claims involving screen actions create an investigation request. Begin with visual episodes overlapping the transcript span, plus a **configurable short lead/lag window** (a 3–4-second window is an experiment, not a fixed rule). Inspect nearby before/after changes; expand progressively if needed. Do not assume an action happened exactly when it was uttered.

**Trigger C: Unexplained visual activity.** Independent change clusters, including those occurring during unrelated speech, are candidates even if no transcript trigger exists. Group motion/scrolling to avoid inspecting every frame; route representative episodes for low-cost review when feasible. Small local value/status/formula changes must not be discarded merely because their pixel area is small. Do not assume unexplained implies erroneous or relevant.

### Phase 3 — Unified timeline and on-demand tools

Merge triggers by time overlap and shared screen region or probable event identity; attach all triggering reasons. The reasoning component receives a **compact timeline**, transcript, existing context maps, and an explicit budget. It can call tools such as:

- `get_context_frame(timestamp)` — legible full screen at a precise timestamp.
- `get_changed_regions(t1, t2)` — diff metadata and region bounding boxes; include source frames when requested.
- `get_crop(timestamp, region, padding)` — high-resolution crop with enough surrounding labels to interpret it.
- `compare_frames(t1, t2, region?)` — aligned before/after visual evidence.
- `inspect_episode(start, end, detail_level)` — keyframes, event candidates, and relevant transcript for an interval.
- `expand_interval(start, end, lead, lag)` — retrieve broader context when earlier evidence is insufficient.

Return stable evidence IDs, timestamps, and coordinates with every tool result. Allow the agent to request a full frame if a crop lacks headers/row labels. Use a deterministic cache so repeated calls do not cause repeated decoding or unnecessary model charges.

### Phase 4 — Evidence-oriented reasoning

For every candidate, decide whether it is:

- **Explained action:** narration sufficiently explains the demonstrated action.
- **Observed but unexplained:** action visible, selection/rule not explained.
- **Unresolved requirement:** rule/threshold/actor/condition needed to implement but not established.
- **Potential conflict:** apparently inconsistent sources that may be compatible under different scopes; ask first.
- **Unsupported assertion:** speech claims behavior not demonstrated by supplied evidence; absence of a screen demonstration is not automatic disproof.
- **No finding:** routine navigation, legitimate variation, or insufficient basis for a material concern.

Each surfaced finding must contain a statement calibrated to available evidence, one or more precise source references, an implementation relevance explanation, and a targeted clarifying question where useful. Avoid treating typical industry practices as mandatory company-specific steps.

### Phase 5 — Stopping, evaluation, and delivery

- Apply configurable limits for vision calls, image-token budget, image count, interval expansion, and per-episode retries. Stop if new inspections are unlikely to change a finding; track low-confidence unresolved questions rather than inventing answers.
- Provide a dry-run mode showing candidate visual episodes **without calling a vision model**.
- Log triggers, frames/crops selected, inferred events, rejected candidates, tool calls, latency, and provider cost.
- Compare at least three methods on identical videos: **fixed-frame baseline**, **simple single-model multimodal baseline**, and **adaptive ProcessGhost**. Give each equal underlying evidence availability and specify whether they use different inspection budgets.
- For the pilot, use the existing ~24-second Excel actualization demo plus two distinct 30–60-second recordings. Record planted/known omissions, legitimate actions, observed-screen-only operations, evidence correctness, precision, recall over *detectable* targets, and false positives.
- Treat tiny sample results as a **POC**, not statistically robust accuracy estimates.

### Required deliverables

1. Repository-specific integration plan and explicit assumptions.
2. Data schemas for frame index, changed regions, episodes, transcript spans, investigation requests, evidence references, and final findings.
3. Minimal functioning implementation with the three triggers and bounded retrieval tools.
4. Configurable knobs and sensible conservative defaults, accompanied by a tuning plan.
5. Debug visualization or trace showing why a region/event was investigated.
6. Unit tests for merging/caching/timestamp alignment and regression cases for tiny edits, full-screen navigation, scrolling, and transcript misalignment.
7. Baseline comparison with **measured** inference counts, estimated or actual cost, missed events, and false-positive behavior.
8. Updated architecture and decision log whenever a design hypothesis is validated or overturned.

**Do not claim that adaptive selection is more accurate or cheaper until measured.** Keep an explicit list of open questions and architectural alternatives.

---

# Part II — Proposed architecture

## 1. Problem statement

A fixed collection of video frames can be expensive to interpret with a vision LLM, particularly as discovery recordings become longer. Yet naively discarding frames risks missing a **small but consequential** approval change, spreadsheet cell edit, or system state transition. Narration can identify relevant windows, but SMEs often act without narrating the details they take for granted.

The goal is not to understand every pixel. It is to preserve enough evidence to reconstruct material steps and **ask the right questions** while spending vision inference selectively.

## 2. Architectural principle

> **The video is a time-indexed, searchable source of evidence, not a preselected bag of images injected into the model.**

Low-cost indexing finds candidate windows and regions; contextual reasoning chooses what needs closer inspection. A multi-trigger index is needed because no single signal—pixel change, transcript, or agent attention—can reliably cover silent and verbally described actions.

## 3. Component diagram

```text
                        VIDEO                          TRANSCRIPT
                          |                                |
              local frame decoding                 timestamped analysis
                          |                                |
                diff + region index               actions / uncertainty
                          |                                |
             episode grouping + cache                     |
                          |                                |
            +-------------+--------------+-----------------+
            |             |              |
       A. context     B. transcript   C. unexplained
        transition      uncertainty     visual activity
            |             |              |
            +-------------+--------------+
                          |
               deduplicated event timeline
                          |
                  reasoning agent
                  (bounded tool calls)
                          |
        context frame / crop / diff / episode / expand
                          |
             grounded workflow observations
                          |
              Writer → Reviewer → Checker
                (adapt to actual codebase)
                          |
               findings + evidence + questions
                          |
              evaluation / cost / trace logs
```

**Important:** The three triggers are *candidate investigation mechanisms*. They do not each require an LLM or a distinct service. A trigger is not itself a finding.

## 4. Interface-context memory

When a new screen appears, create a compact context record:

- Application and visible worksheet/page identity (if legible).
- Tables, columns, labels, buttons, navigation, and visible record identifiers.
- Relevant entities, e.g., vendor, invoice, month, amount, approval status.
- Explicitly visible relationships and **separately marked hypotheses**.
- Evidence ID, timestamp, confidence, and applicability period.

Use the context map to interpret later crops, because a crop showing `₹45,000` without its column and row headers is often uninformative. Refresh the context when the UI genuinely changes, not every time pixels differ.

## 5. Time and alignment

Treat video timestamps and transcription timestamps as potentially misaligned; preserve both and record any calculated offset. A spoken action may precede or follow its on-screen execution. Initial retrieval windows should be narrow but expandable. Align by overlapping **episodes**, not just exact speech times.

## 6. Cost and coverage controls

- Local high-frequency frame comparisons do **not** imply high-frequency model inputs.
- Prefer **one full-context frame** per meaningful UI context and **high-resolution cropped pairs** for local edits, escalating to wider context as required.
- Cluster consecutive diffs, especially scrolling and animation.
- Cache evidence retrieval and model interpretations where justified.
- Do not throw out tiny changes solely by pixel-area filtering; track region-level changes and keep periodic fallback checkpoints.
- Set inspection budgets, but record what remains unexamined so cost controls do not masquerade as negative evidence.
- Cost objective: **meaningful, correctly grounded findings per model dollar**, constrained by missed-important-event rate and false positives.

## 7. POC test case reference

The existing approximately 24-second Excel demonstration shows a speaker navigating among master data, transaction data, and Monthly Actuals, transferring an AWS amount, and observing calculated results. Candidate points for clarification include **how source and destination rows are matched**, **how period is selected**, and **which calculations/variance rules matter**. Those are *candidate questions*, **not confirmed missing business rules**. The brief recording cannot independently establish the entire true workflow.

Build two additional 30–60-second clips with **different** failure mechanisms (for example, an exception-handling path and an approval condition). Define the true process separately before recording, and withhold that truth from the detector.

---

# Part III — Architecture decision records (ADRs)

These records are intended to stop future assistants or developers from inadvertently reopening rejected approaches without new evidence. **Discussion and reasoning are documented here; no rejected option is claimed to have been empirically tested.**

## ADR-001 — Adopt three independent visual investigation triggers

**Status:** Proposed / selected direction; implementation pending.

**Decision:** Use (A) screen-context transitions, (B) transcript uncertainty, and (C) independent unexplained visual activity as separate trigger sources, then merge investigations.

**Why:**

- Context changes establish what application, sheet, fields, and business entities are currently visible.
- Transcript-triggered retrieval targets spoken ambiguities and claims efficiently.
- Visual-activity triggers capture actions SMEs never mention, even while speaking about another topic.
- Any one trigger alone has blind spots.

**Tradeoff:** More orchestration, deduplication, and coverage accounting than a fixed sampling pipeline.

**Revisit if:** Measurements show one trigger contributes negligible unique useful findings relative to its cost.

## ADR-002 — Use pixel differences for candidate discovery, not importance ranking

**Status:** Proposed / selected direction.

**Decision:** Compute local frame/region changes; do not infer workflow importance from visual change magnitude.

**Why:** Opening a dropdown may change a large part of the page without changing business state. Editing one small approval label, payment amount, or formula can be far more consequential than the number of pixels suggests.

**Tradeoff:** Need a later semantic layer, plus careful attention to small diffs and noise.

**Revisit if:** A validated UI-specific model or instrumentation provides a reliable semantic score without expensive vision calls.

## ADR-003 — Reject the proposed scalar priority score as an initial design

**Status:** Rejected as a design choice, **not experimentally evaluated**.

**Earlier proposal:** `P = w1 * D + w2 * T + w3 * U`, where `D` was visual difference magnitude, `T` transcript relevance, and `U` lack of explanation.

**Reason rejected:**

1. `D` has weak relationship to workflow importance; high-change dropdowns can outrank low-change approval edits.
2. `U` often cannot be known until the changed region has been semantically understood; using it to decide what to inspect is circular.
3. A neat scalar score implies precision and comparability that the variables have not established.
4. A weighted score can prematurely suppress high-value silent actions.

**Replacement:** High-recall candidate detection, three trigger sources, and context-aware investigation after candidates are surfaced.

**Revisit if:** A labeled dataset supports a calibrated ranking model whose event recall, cost, and false-positive performance beat the simpler trigger system. Do not reinstate the score by renaming its terms.

## ADR-004 — Do not rely solely on transcript-guided frame selection

**Status:** Rejected as exclusive strategy; retained as Trigger B.

**Why not exclusive:** A core ProcessGhost goal is discovering actions that SMEs perform but never explain. Narration-only sampling can miss silent edits and actions unrelated to nearby speech.

**Use retained:** Transcript spans are valuable for selecting initial investigation windows and expanding context on demand.

## ADR-005 — Do not send fixed-rate sampled frames to the vision model by default

**Status:** Rejected as primary scalable approach; retained as baseline.

**Why:** A recording can contain long visually redundant periods. A 2–4-FPS **local image-diff pass** can be cheap, whereas sending 2–4 full frames per second through multimodal inference may be expensive. These two activities must not be conflated.

**Use retained:** Fixed-interval visual inference provides a simple benchmark and fallback for failure analysis; periodic image checkpoints can aid coverage.

**Revisit if:** Fixed frame inference is cheaper, faster, or equally effective on actual recordings after model pricing and engineering overhead are included.

## ADR-006 — Do not let the agent decide where to look without an independent visual index

**Status:** Rejected as exclusive approach; agent-controlled retrieval retained.

**Why not exclusive:** An agent driven only by transcript or visible context does not know where unmentioned changes occurred. It may never ask for an important silent interval.

**Use retained:** Once the independent index supplies candidates, the agent decides whether to request crop, full frame, comparison, or expanded time range.

## ADR-007 — Progressive on-demand inspection instead of one-shot exhaustive analysis

**Status:** Proposed / selected direction.

**Decision:** Start with context frames and candidate before/after evidence, then request more only if interpretation remains uncertain.

**Why:** A small edit often needs row/column context, but not a full-video visual pass. Progressive investigation adapts evidence depth to the question.

**Risks:** Repeated requests, added latency, agent looping, and missed evidence if the budget is too strict.

**Controls:** Hard budgets, caching, deterministic logs, overlap merging, and explicit unresolved outcomes.

## ADR-008 — Do not add a separate learned event-priority model yet

**Status:** Deferred, not rejected permanently.

**Earlier idea:** Small decision model to select visually important moments before the main reasoning pass.

**Why deferred:** No labeled examples yet establish importance, the model may require visual understanding nearly as expensive as the work it saves, and an extra component increases complexity and error propagation. First benchmark rule-based candidate generation plus the existing agent's tool-driven investigation.

**Revisit if:** A sizeable labeled corpus of events exists and a lightweight classifier significantly improves cost–recall tradeoff.

## ADR-009 — Distinguish observed evidence from inferred workflow rules

**Status:** Proposed / selected direction.

**Decision:** Store observations, inferences, and questions separately. A visual comparison may support “the amount in this field changed” but may not establish whether the user typed, pasted, recalculated, or had permission to modify it.

**Why:** ProcessGhost must not transform an absence of explanation into proof of a missing mandatory process. The pilot should penalize unsupported certainty.

## ADR-010 — Cluster visual activity rather than inspecting every diff

**Status:** Proposed / selected direction.

**Why:** Scrolling, motion, cursor movement, and animations may generate many changes for one navigational event. Grouping cuts redundant model calls; retain enough boundaries and small edits to avoid losing consequential state changes.

**Risk:** Over-aggressive clustering may hide an edit within a scroll sequence. Validate on synthetic small-change regression clips.

## ADR-011 — Adapt the current Writer → Reviewer → Checker rather than expand agents automatically

**Status:** Integration hypothesis; pending repository review.

**Why:** The existing multi-stage reasoning path may already provide semantic interpretation and checking. A new visual-indexing layer need not imply three new LLM agents. Additional roles must earn their cost and reliability through measurements against a strong single-model baseline.

## ADR-012 — Benchmark against simpler alternatives before claiming value

**Status:** Selected evaluation principle.

**Decision:** Compare adaptive ProcessGhost with fixed-frame and well-prompted single-model baselines under clearly documented evidence and budget conditions.

**Why:** Multi-stage architecture is not value by itself. Evaluate precision, recall over detectable issues, timestamp correctness, false positives, question usefulness, latency, and measured inference cost.

---

# Part IV — Open questions and planned experiments

These are *not settled decisions*:

1. **Diff detector:** What combination of frame differences, SSIM, ROI masks, and temporal thresholds captures tiny cell changes without flooding the index with cursor/scroll activity?
2. **Episode boundaries:** How should fast navigation, scrolling, and editing be clustered without collapsing a consequential state transition?
3. **Context freshness:** When does an app or sheet need another full frame? Is a changed window title enough?
4. **Crop context:** How much padding, nearby row/column labels, or previous context map is needed for trustworthy interpretation?
5. **Time alignment:** How far should transcript-triggered windows expand when speech and actions are separated by several seconds?
6. **Agent budget:** What call count and cost limit provides acceptable missed-event recall for 30–60-second clips and for longer recordings?
7. **Runtime choices:** Can inexpensive on-device OCR or UI-structure extraction assist, and at what error/cost? Do not presume OCR accuracy.
8. **Video scaling:** How does the approach handle scene changes, screen sharing latency, multiple windows, small text, and low frame rate?
9. **Fairness:** Does the proposed architecture actually outperform a single multimodal model allowed to request comparable evidence?
10. **Integration:** Which existing ProcessGhost module should own event indexing, context maps, retrieval tools, and evidence checking?

## Minimal validation matrix

| Test | Required behavior | Failure to watch |
|---|---|---|
| Entire worksheet changes | New context frame + usable interface map | Treating layout movement as a business action |
| Single approval-status cell changes | Retain tiny localized candidate | Discarding edit due to low pixel percentage |
| Dropdown opens then closes | Group as interaction; assess if value actually changed | False claim of committed selection |
| SME says “paste here” | Link nearby source and destination evidence, expand if necessary | Inferring mapping rule as certain |
| Unnarrated edit during unrelated speech | Trigger C finds the event | Transcript-only blind spot |
| Long scroll | One/few episodes rather than hundreds of vision calls | Diff storm and runaway costs |
| Spoken step not visually demonstrated | Mark as unverified, not disproved | “Missing step” false positive |
| Existing context appears again | Reuse mapped context if still applicable | Repeated expensive full-frame analysis |

## Change-management rule for future agents

Before proposing an alternative already covered in these ADRs:

1. Cite the relevant ADR and accurately restate its rejection/deferment rationale.
2. State **what new evidence, constraint, or measurable improvement** changes the earlier decision.
3. Propose a falsifiable experiment against the selected approach.
4. Update the ADR status and results only after the experiment; never retroactively claim an untested approach was tried.

**Current confidence:** This is a reasoned architecture proposal from design discussion, not proof of comparative performance. Implementation should be incremental and evidence-driven.


---

# Part V -- Status in this repository (added 2026-10-18)

Parts I to IV above are the design document as written. This part says what the repository now does about
each of its ideas, what was changed from the plan, and what has and has not been measured. Nothing here
claims the adaptive layer is cheaper or more accurate: no comparison has been run.

## What exists

| Design element | Where | State |
|---|---|---|
| Cheap local index, no per-frame vision calls | `core/screen/timeline.py`, `diff.py` | Built before this work (3 FPS local diffs, settle-based events, before/after keyframes) |
| Small changes never dropped (ADR-002) | `core/screen/regions.py`, used by `records.py` and `evidence.py` | Built. Boxes are merged by proximity, never ranked by area. Synthetic tests only |
| Scroll with an edit inside it; interaction that came and went | `timeline.py`: `residual_bboxes`, `transient`, `peak_frame` | Built. Synthetic tests only |
| Two quick actions in different places (ADR-010 risk) | `SCREEN_SPLIT_ON_LOCUS_SHIFT` | Built but OFF by default: it contradicts an existing test and is unmeasured on real data |
| Trigger A, context map (ADR-001) | `core/screen/surfaces/`, `context.py`, `speech_clue.py` | Built for spreadsheets. Web profile built, synthetic tests only |
| Trigger B, transcript uncertainty | Reviewer and Reconciler flags with attached events, folded into `investigate.py` | Pre-existing mechanism, now reported as a trigger |
| Trigger C, unexplained activity | Events no step explains, `investigate.py` | Built |
| Merged triggers, coverage accounting | `core/screen/investigate.py`, `SOPDocument.screen_coverage`, report section | Built. Synthetic tests plus a real-clip dry run |
| Retrieval tools with stable ids and a budget | `core/screen/retrieve.py`, `evidence_id.py` | Built and tested as a library. Not called by a pipeline run |
| Observed vs inferred vs unresolved (ADR-009) | `Flag.finding_kind`, `report/build_spec.py: finding_kind` | Built. Deterministic; no model call |
| Dry run with no model calls | `scripts/evidence_dry_run.py` | Built and run on the real clip |
| Baselines | `evals/baseline_single_model.py`, `evals/compare_methods.py` | Written and unit-tested with fakes. NOT run: they cost money |
| Regression matrix (Part IV) | `tests/test_screen_timeline.py`, `test_regions.py`, `test_contexts.py`, `test_investigate.py`, `test_retrieve.py`, `test_surfaces.py`, `test_finding_kind.py` | Built on synthetic clips; one gated real-clip check (`tests/test_score_surface.py`) |

## How the design-document decisions map onto the repository's ADRs

| Design ADR | Repository decision |
|---|---|
| ADR-001 three triggers | `adr/0011` |
| ADR-002 pixel difference for discovery, not importance | `adr/0010` |
| ADR-003 no scalar priority score | Respected; stated in `adr/0011` |
| ADR-004 not transcript-only | Same reasoning as `adr/0006` |
| ADR-005 no fixed-rate frames to the vision model | Same; the fixed-frame v2 path is kept as a benchmark |
| ADR-006 agent needs an independent index | Same reasoning as `adr/0006`; the index feeds `adr/0011` |
| ADR-007 progressive bounded inspection | Deterministic two-level escalation (`adr/0007`) plus the budgeted library (`adr/0012`) |
| ADR-008 no learned event-priority model yet | Respected; nothing added |
| ADR-009 observed vs inferred | `Flag.finding_kind`, decision X10 in `25-decision-register.md` |
| ADR-010 cluster activity | `adr/0010` (residual boxes, transient events, optional locus split) |
| ADR-011 adapt the existing agents, add none | Respected: no new model call anywhere in this work |
| ADR-012 benchmark before claiming | Decision X11; harness written, not run |

## Where the build departs from the plan or the design text

- The design asks the agent to call retrieval tools. ADR 0007 had already rejected model-requested context.
  The tools exist as a deterministic library and a model-driven loop is an open experiment (question Q13).
- A hosted vision reading for unrecognised surfaces was in the surface plan. It is not built: it cannot be
  validated without credit and a web recording, and it would send screen images to a provider.
- The speech clue window was first written looking 5 s back and 2 s forward. The measured lag runs the
  other way (speech follows the action by 4 to 5 s), so it is 2 s back and 5 s forward.
- The locus split is off by default (see above).
- Evidence ids are content-based (video fingerprint, kind, time, box), so they are reproducible across
  runs of the same file. The Checker's evidence pack records its ids in the trace; the Checker does not
  fetch evidence through the library.

## Measured so far (one 24 s spreadsheet clip, no model calls)

- 7 events became 7 investigations (3 context transitions); 3 contexts, the second reused on return.
- Every scored field of the draft surface labels matched (change kind 6/6, sheet position 7/7, sheet name
  3/3, selected cell 5/5). The labels are drafts and not independent of the detector (`expected_surface.json`).
- OCR frequently garbles or drops the formula-bar glyph; the selected cell was recovered anyway, formula
  content only occasionally, and the third sheet showed no readable cell reference.

## Measured with a model (one OpenAI run, 2026-10-18, run 20261018-105107-eb1a0a)

- `--screen` on the real clip: $0.135, 0 failed flags, 11 flags. The keyword scorer found 4 of 6 required
  findings (earlier runs: 0 to 3 of 6), no forbidden claims, no false "nothing is missing" banner. The two
  missed were the matching column and the "Paid" status value. This is ONE run and the scorer is crude;
  run-to-run variance on this clip is known to be large.
- The report's coverage section showed every investigation examined.
- The run exposed a flaw in the first investigation merge: one wide transcript flag bridged all events into a
  single investigation spanning the whole recording. Fixed (a flag now adds its reason to each event it is
  attached to); replayed offline on that run: 8 investigations.
- Not a comparison with any baseline.

## Not measured

Cost or accuracy against the fixed-frame and single-model baselines; repeat-run variance of the above; the
examined/skipped statuses when a cap or budget actually bites; any web or ERP surface; the locus split. The
Claude provider (`core/llm/client.py`, `provider="anthropic"`) is built and tested with fakes only: no Anthropic key was
available, and the installed langchain-anthropic uses an older structured-output request that may need an
upgrade (`python -m scripts.smoke_anthropic` tests it). The open questions from Part IV are tracked as Q10 to Q14 in the decision register.

## Run it

    python -m scripts.evidence_dry_run ../data/english_hackathon_test.mp4            # no model calls
    python -m evals.score_surface --case bills_tracker --records runs/<id>/change_records.json
    python -m evals.compare_methods --score v2=runs/a adaptive=runs/b single=runs/baseline-c   # table only

## Update 2026-10-22: what was added on top of this layer

The next layer is described in `adr/0014-flow-from-said-and-shown-with-an-evidence-ladder.md`: events are classified by what changed (not by pixel size), selections become anchors tied to speech and later values, unnarrated navigations and data changes become "shown" steps, every data step must answer source / key / target / effect, and every open question climbs video, then a read-only document rung, then the person who recorded the process. The flow is drawn with its gaps (`flow.json`, `flow.md`).

Verified on the one real clip (local OCR only, no model calls, `scripts/evidence_dry_run.py`): the classifier matched 6 of 7 draft event-class labels, classified no data change as droppable, and the anchor join found the copy source from the value that reappeared in the paste. The 7th (a selection with no readable cell change) is kept as "unclear". The labels are drafts a person has not confirmed. Verified on the real workbook with no model: the profiler returns the formula that states the rule for the total and the lookup tools show that no row holds the value the formula needs. Everything model-driven (slot questions, Reconciler actions and slot fills, the document loop, the cheap-model read) is unit-tested with fake clients only.

## Update 2026-10-22 (later): first real runs of the flow and ladder work

Two real runs on the one clip, with the workbook (`--screen --workbook`):

- Run 1 (before the cell trail, the facts, the claim guard and the match check): 3 of 6 findings, $0.54, 30 model calls on the workbook step for 18 questions. The row key was never answered, one answer cited the wrong month's row, and the Checker attached the billed total (235,000, a real number on screen) to the actualized total one row below it.
- Run 2 (after): 9 of 10 findings on the corrected list, no forbidden claim, $0.31. The row key became a confirmation between Transaction String and Bill ID with their distinct counts; the effect of the paste was answered from formulas (G18 and F27, whose total counts only rows with Status "Paid", none of which has it); the unmet condition was raised as a blocking question.
- Built afterwards and replayed on the saved run, not yet re-run with a model: a step with no located target uses the one cell the recording changed; only the four data-step questions go to the model loop by default; repeat detection ("1 of the 8 cells in F18:F25 changed").

The scenario facts were corrected by the owner (row key is the Transaction String matched in column B of the transactions sheet; the amount is taken from its column C; Status "Paid" is not in the recording). The workbook open in the video differs from the file supplied: the match check reports it as partial. Still one clip, one workbook, one narrator.
