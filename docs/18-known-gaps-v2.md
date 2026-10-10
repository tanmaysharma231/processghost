# 18 - Known Gaps (v2 pipeline)

This is the defect register for the current Python v2 Writer / Reviewer / Checker pipeline
(`processghost/`, see `13-current-logic-and-architecture.md` and `14-writer-reviewer-checker.md`):
what the code does, the evidence, the impact, and whether it needs fixing.

---

### 1. The Reviewer's coverage check is unstable -- HIGH, partially fixed and confirmed against real runs

**Reality.** `merge_reviewer_output()` (`core/llm/parsing.py:76`) is the pipeline's core structural
gap-detection mechanism: it asks the Reviewer to name what carries between two consecutive steps,
and turns every consecutive pair the model leaves unbridged into a structural `Flag`. This is
deliberate -- see `docs/16-prompt-guide.md` lesson 1 -- because a model asked "is this ok?" is
strongly biased toward saying yes, so absence-of-answer was chosen as the signal instead of
self-report.

**The measurement.** Across 18 runs of the **same** video (`data/english_hackathon_test.mp4`),
structural flags ranged **0 to 5** and emitted transitions ranged 0 to 3. Since the input never
changed, the true number of gaps in that recording is fixed -- the entire spread is model noise.
In 6 of 18 runs the Reviewer emitted **zero** transitions, and the coverage check read that
silence as "every consecutive pair is a gap." Separately, in the most recent run all 4 flags came
from the Reviewer's four ambiguity checks and **zero** came from the coverage check -- the
mechanism documented as the core gap-detection method contributed nothing on the scenario this
project validates against.

**Why the obvious fix is wrong.** The tempting patch is to special-case zero transitions and stop
flagging every pair when that happens. Do not do this. If a SPOC explains a process badly enough
that genuinely nothing connects, zero transitions is the *correct* answer and flagging every pair
is the *correct* response -- that is precisely the case this product exists to catch. Suppressing
it would silently delete a true finding in the worst-explanation scenario. Two very different
situations produce an identical signal today: an incoherent explainer (a true finding) and a lazy
or derailed model response (noise), and nothing in the code or the rendered output can tell them
apart.

**Impact.** The gap-detection mechanism the project treats as its core differentiator is currently
unreliable enough that its output cannot be trusted run-to-run, and on the one real validated
scenario it contributes nothing at all.

**Fix landed.** Every transition is now grounded in a verifiable quote: `REVIEWER_SYSTEM`'s
`<transitions>` section requires the Reviewer to quote the exact transcript span that establishes
the connection, alongside naming what carries, and `merge_reviewer_output` (`core/llm/parsing.py`,
`_quote_is_grounded`) validates in code that the quote actually occurs in the transcript before
counting the pair as covered -- a claimed connection with an invented, paraphrased, or missing
quote is treated exactly like silence and does not count as coverage. `StepTransition` and
`ReviewerOutputTransition` (`core/models/sop.py`) both gained a `quote` field. Unit-tested
(`tests/test_pipeline_reviewer.py::test_transition_with_fabricated_quote_is_treated_as_omitted`
and the grounded-coverage tests alongside it) with no API spend. This also makes the bad-explainer
case *provable* -- "no pair yields a valid quote" is now a reportable finding instead of an
ambiguous silence.

**The real acceptance run -- done, 2026-08-30, `config.LLM_PROVIDER = "openai"`, 3 runs of
`data/english_hackathon_test.mp4` (run ids `20260830-155412-ab0056`, `20260830-160025-f5664c`,
`20260830-160732-e1b16b`, ~$0.24-0.38 each).** The honest result: quote-grounding did **not**
collapse the spread. All three runs had the same 6-step draft (5 consecutive pairs); structural
flags came back **3, 6, 4**. Checking `trace.jsonl`'s `payload` events (the raw Reviewer output,
now captured verbatim -- see entry 3) confirmed the 6-flag run's zero transitions was **not** a
grounding rejection catching a fabricated quote; the Reviewer genuinely emitted zero transitions
in its raw output, same failure mode measured before this fix. Quote-grounding closes exactly the
gap it was built for -- an invented or paraphrased quote no longer counts as coverage -- and does
nothing for a model that skips the transitions task entirely. Those are different failure modes;
only the first one was ever in scope here.

**What the reliability warning (`sop_render.py: _reliability_warning`) actually did, on the same
three runs:** fired on exactly the one run with zero transitions (the 6-flag run), stayed silent
on the other two (3 and 4 flags, both with real grounded transitions). That is the mechanism
working as designed -- it is the honest signal this fix was actually meant to produce: not a
stable flag count, but a run correctly telling you when its structural findings can't be trusted,
instead of presenting 6 flags with the same confidence as 3. That distinction is the real fix;
"the count stabilized" was never going to happen from grounding alone, and claiming it did would
be overselling this session's own data.

**Resolved: repeated Reviewer sampling is not being built now.** The zero-transition case still
occurred in 1 of 3 real runs (33% -- consistent with the ~33% rate seen in the original 18-run
baseline), so the underlying model behavior is unchanged. But it no longer needs suppressing via
sampling, because the reliability warning already correctly flags it when it happens. Sampling
would reduce *how often* the warning has to fire; it would not fix anything the warning doesn't
already handle. Revisit only if the warning itself is found to under- or over-fire in practice.

**A clarification for whoever reads the code:** a transition row emitted with an empty `carries`
is *not* discarded -- `merge_reviewer_output` routes it onto the same path as an omitted row, so it
correctly becomes a structural flag. The instability is never about a blank answer getting lost; it
is that a blank answer and a missing answer are indistinguishable from a model that simply stopped
early.

---

### 2. `MAX_CHECKED_FLAGS` -- decided: leave as is -- LOW, informational

**Reality.** `config.MAX_CHECKED_FLAGS = 20` caps how many flags the Checker resolves against real
video evidence per run (`core/pipeline.py:157`, `_select_flags_for_check`); the rest are marked
`unchecked` rather than silently dropped. It exists as a cost guard -- the Checker is the only
expensive stage (~$0.04-0.06/flag in frames/tokens, vs. ~$0.008 for a whole Reviewer call).

**Evidence it has never mattered.** The highest flag count any run has produced, across all 18
recorded runs, is 9. The cap has never once bound.

**Impact.** None currently. Its only real defect was that truncation used to be recorded as a
quiet inline `status: unchecked` string easy to miss when reading `sop.md`; the four-section
renderer (`report/sop_render.py`, see Section 3, "Not checked") now surfaces it under an explicit
"pipeline limitation, not a confirmed gap" heading.

**Fix required.** No. Recorded here so the question isn't re-opened from scratch in a future
session: the cap is a harmless runaway guard, not a live constraint on the deliverable.

---

### 3. `trace.jsonl` used to carry only call metadata, never the actual output -- fixed

**Reality (before this fix).** `Metrics.add()` (`core/llm/client.py`) wrote `stage`/`model`/
`usage`/`cost_usd`/`latency_ms` per call, live, to `runs/<run_id>/trace.jsonl` -- but
`structured_chat()`'s actual return value (the parsed `WriterOutput`/`ReviewerOutput`/
`CheckerOutput`) never reached it. The trace said a Checker call finished and what it cost; never
what it resolved.

**Fix.** `Metrics.record_payload(stage, payload)` writes a second, separate trace line
(`event: "payload"`) carrying the real structured output, called from both
`LLMClient.structured_chat` and `SarvamClient.structured_chat` right after the result validates
against its schema -- kept as its own event rather than folded into `add()`'s existing "call"
event specifically so cost/token accounting can never be duplicated by it. Unit-tested
(`tests/test_metrics.py`), zero effect on any existing caller since it's purely additive.
`structured_chat` also gained an optional `payload_extra` kwarg -- the Checker call site passes
`{"flag_id": flag.flag_id}` through it, since `CheckerOutput` itself has no `flag_id` field (it's
a per-flag call; the flag is known by the caller, not something the model was asked to return),
and a live trace consumer otherwise has no way to tell which flag a given Checker payload
resolved. Confirmed against real captured trace data (`AppTest`-driven smoke test over
`runs/20260830-155412-ab0056/trace.jsonl`) before and after this addition -- no exceptions either
way, backward compatible with trace lines recorded before the fix.

**Why this mattered beyond the live-progress page it was built for:** it's what made this
session's real-run investigation of entry 1 possible without guessing -- reading the `payload`
event for the Reviewer stage is how the 6-flag run above was confirmed to be a genuine
zero-transition response and not a grounding rejection. The mechanism is now also the backbone of
`app/streamlit_app.py`'s live progress view, which tails `trace.jsonl` and renders each call's
real payload as it lands.

---

### 4. The Checker's OpenAI calls hit rate limits in every real run this session -- MEDIUM, not investigated

**Reality.** Across the 3 real acceptance runs for entry 1, 5 of ~19 total Checker calls failed
with `OpenAIRateLimitError` (via `RetryError` after 3 attempts, `core/llm/client.py`'s
`@retry(stop_after_attempt(3), wait_exponential(...))`), landing those flags as `check_failed`
rather than a real verdict. This is a real-world operational gap, not a logic bug -- the retry
policy isn't aggressive enough for whatever rate limit this project's OpenAI account/tier
actually has, especially given the Checker's calls are large (up to `CHECKER_MAX_FRAMES_PER_FLAG`
=16 images per call) and can fire in a tight loop.

**Impact.** On a real run, a meaningful fraction of flags can silently degrade to `check_failed`
purely from being throttled, not because the evidence was actually insufficient -- indistinguishable
in the rendered document from a flag the Checker genuinely couldn't resolve, even though the two
have very different remedies (retry later vs. accept the gap).

**Fix required.** Not decided. Candidate directions: longer backoff / more attempts specifically
for rate-limit errors (vs. other failures), a small delay between per-flag Checker calls, or
surfacing `check_failed`-due-to-rate-limit distinctly from `check_failed`-due-to-something-else in
the document. Not fixed this session -- recorded so it isn't mistaken for a Checker logic problem
if seen again.
