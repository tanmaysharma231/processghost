# 16 - Prompt guide

Lessons for writing/rewriting any prompt in `core/llm/prompts.py` (`WRITER_SYSTEM`,
`REVIEWER_SYSTEM`, `CHECKER_SYSTEM`), so they don't have to be re-derived next session. Two parts:
what the published guides from Anthropic and OpenAI actually say, checked directly rather than
assumed; and what this project's own iteration cycles taught, each tied to where it was learned.

## External guidance, checked directly

Checked against Anthropic's [prompting best
practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)
and OpenAI's [GPT-4.1 prompting
guide](https://developers.openai.com/cookbook/examples/gpt4-1_prompting_guide) rather than going on
instinct. Both converge on the same fixes:

- **Lead positive, don't stack prohibitions.** Anthropic names this directly: *"Tell Claude what to
  do instead of what not to do."* OpenAI's guide says the same. Neither says "never use a negative"
  -- Anthropic's own sample prompt for this technique still keeps one `DO NOT` line as backup
  reinforcement underneath the positive framing. The finding is "positive first, one negative as
  backup if truly needed," not "positive only."
- **Structure mixed content with clear sections.** Anthropic: *"XML tags help Claude parse complex
  prompts unambiguously, especially when your prompt mixes instructions, context, examples, and
  variable inputs."* OpenAI's guide gives a canonical template via markdown headers instead: `#
  Role and Objective` / `# Instructions` / `## Sub-categories` / `# Reasoning Steps` / `# Output
  Format` / `# Examples`. Either works; pick based on which model family actually reads the prompt
  (below).
- **State the "why," briefly.** Anthropic: *"Providing context or motivation behind your
  instructions... can help Claude better understand your goals... Claude is smart enough to
  generalize from the explanation."* The lesson isn't to cut the reasoning -- it's to keep it to one
  sentence instead of a paragraph, so it doesn't dilute the instruction it's attached to.

**Check which model actually reads the prompt before applying model-specific advice.** None of
this project's prompts are ever sent to Claude -- `WRITER_SYSTEM`/`REVIEWER_SYSTEM`/`CHECKER_SYSTEM`
go to `gpt-4o` (OpenAI) or `sarvam-105b` (Sarvam) only, via `structured_chat` in
`core/llm/client.py` / `core/llm/sarvam_client.py`. OpenAI's own guidance is the more directly
applicable source for this codebase's prompts; Anthropic's is worth following when a prompt needs
to stay broadly compatible across model families (XML tags parse fine for GPT/Sarvam too, just
aren't their vendor's own top recommendation).

## This project's own lessons

Each of these came from a real iteration cycle on a real prompt in this codebase, verified against
the actual pipeline, not just reasoned about in the abstract.

- **Omission alone was not enough -- ground the answer in something checkable.** The original
  version of this lesson was "don't give a model a boolean field to self-report a judgment ('is
  this transition ok?'); instead make the *absence* of an answer the signal, and count it
  deterministically in code." That much still holds -- `REVIEWER_SYSTEM` never asks "is this
  broken," it asks the model to name what carries between two steps *only when it honestly can*.
  But omission by itself turned out not to be a stable signal in practice: measured across 18 runs
  of the identical video, the Reviewer's silence swung between 0 and 5 structural flags, because
  silence has more than one cause -- a genuinely unbridgeable pair and a model that simply didn't
  do the work look identical as an omitted row. See `docs/18-known-gaps-v2.md` entry 1 for the
  full measurement. The fix: every transition must now also carry a `quote`, the verbatim
  transcript span it's grounded in, and `merge_reviewer_output`'s `_quote_is_grounded`
  (`core/llm/parsing.py`) verifies that quote is an actual substring of the transcript before
  counting the pair as covered. A transition with an invented, paraphrased, or missing quote is
  now treated exactly like an omitted row -- omission-as-signal survives, but it's no longer taken
  on the model's word alone.

- **Force observation before conclusion.** A model asked for a verdict and an answer in the same
  breath can jump straight to a plausible-sounding conclusion with nothing grounding it. Asking it
  to write down a concrete, checkable intermediate fact *first* -- what carries between two steps
  (plus, now, the quote that grounds it), what's literally legible on screen -- makes the
  conclusion answerable to something already on the page, not free-floating. Used twice: the
  Reviewer's `carries`/`quote` fields precede any flag; the Checker's `observed` field precedes
  `verdict`/`inferred_text` (both `core/llm/prompts.py`).

- **One instruction can't cover two different answer shapes.** The Checker's first version asked
  one "is this specific enough" test for every flag. It quietly meant two different things: an
  ambiguity flag's honest answer is a *location* (a column, a sheet), a structural flag's honest
  answer is an *action* (something the screen shows happening). One shared test half-fit both and
  let vague structural answers slip through as "resolved." Fixed by branching the specificity test
  on `flag.family` explicitly, inside `CHECKER_SYSTEM`'s `<verdicts>` section, rather than writing
  one instruction and hoping a single phrasing would generalize to both cases.

- **Explicit, repeated permission to say "I don't know."** A schema with an `unresolvable`/unsure
  option still gets avoided in practice unless the prompt states outright, in its own sentence, that
  using it is a normal and expected outcome -- not implied by the option merely existing in the
  schema. `CHECKER_SYSTEM` says this twice: once in `<context>` ("you are not expected to know what
  was in their head... if you are unsure... say so plainly and answer 'unresolvable'") and once
  again inside `<verdicts>`'s own definition of "unresolvable."

- **A structural test can catch a leaked assumption a careful read missed.** The scenario-agnostic
  grep guard (`tests/test_prompts_scenario_agnostic.py`) caught real scenario answers leaking into
  illustrative examples twice this session, in prompt text that had just been written and read over
  by hand before being caught. Worth having this kind of guard for any project fact that must never
  reach a prompt, not just trusting a careful read.

- **A "do not invent" scope line has to be aimed at the actual failure mode, not copied verbatim.**
  `WRITER_SYSTEM` and `REVIEWER_SYSTEM` each carry a `<scope>` line reading "do not invent a
  sheet/column/file name beyond what was literally stated." `CHECKER_SYSTEM` was missing a `<scope>`
  section entirely for a while -- and copying that same wording over would have been actively wrong,
  since naming the exact column is the Checker's whole job. Its `<scope>` instead binds every
  specific name to the *evidence*: "every specific name... must be legible in the evidence you were
  given... a plausible name you cannot actually read is the signal to answer 'unresolvable'." Same
  underlying goal (don't fabricate specifics) aimed at a different failure mode (fabricating from
  general knowledge of what spreadsheets usually look like, not fabricating from thin air).

- **Reframe `unresolvable` as a deliverable, not a fallback, if that's what it actually is.**
  `CHECKER_SYSTEM`'s `<context>` doesn't just permit "unresolvable" -- it states the product
  reason for it: the recording is a sample of someone explaining a process, and a question the
  screen can't answer is a real question worth taking back to that person, not a failure to
  resolve. This changed the prompt's framing entirely (earlier drafts treated it as an escape
  hatch); check that a prompt's stated purpose for an "I don't know" outcome actually matches what
  the rest of the pipeline *does* with that outcome (see `docs/18-known-gaps-v2.md` on how
  `unresolvable` flows into the escalation queue) before writing the framing, not after.

- **Unit tests don't catch weak prompt quality -- only a real run does.** Every prompt rewrite this
  session passed its unit tests on the first try; whether it actually improved the model's real
  output was only ever visible after a real acceptance run against the real video
  (`data/english_hackathon_test.mp4`). Budget for that step every time a prompt changes, rather than
  treating green tests as sufficient evidence. The quote-grounding fix above is a live example of
  this still being owed -- see `docs/18-known-gaps-v2.md` entry 1's "not yet done" note.

## Applying this

When rewriting a prompt in this repo: lead with a positive role/task statement, section it (XML
tags, following the pattern above), keep the "why" to one sentence tied to what the output actually
feeds downstream, keep at most one negative reinforcement line for the single most important
constraint, and if the model has to make a judgment call, force it to write down the concrete
fact behind that judgment before asking for the judgment itself. Verify with the fast test suite
first, then a real run against the real video -- never skip the second step.
