# ProcessGhost

Compare what an expert **says** (transcript) vs what they actually **do** (screen actions) in a
narrated screen recording, and surface the gap -- the expertise blindness -- before writing
documentation or automating the process. The recording is treated as a sample of someone
explaining a process, not the source of truth: the pipeline's job is to find what they never said
out loud, so it can be asked back to them directly.

Pipeline: **Writer -> Reviewer -> Checker -> Triage**, four sequential LLM-driven stages producing
one validated `SOPDocument`, rendered into two outputs from the same source
(`sop.md` for a human, `build_spec.json` for a future build agent). See
`../docs/14-writer-reviewer-checker.md` for the full architecture, `../docs/15-provenance-and-doc-
format.md` for the attribution grammar and document format, and `../docs/00-README.md` for the
full ProcessGhost concept and design docs. `../docs/12-real-scenario-target.md` documents the real
verified test case this pipeline is validated against (not optimized for) --
`../docs/18-known-gaps-v2.md` is the current defect register.

- **Writer** (transcript -> draft SOP, one LLM call): converts narration into ordered imperative
  steps, deliberately naively -- never repairs a vague reference. If the narration says "paste it
  here," the step says "paste it here." The one FATAL stage: no fallback, since a run with no
  document has nothing for later stages to work with.
- **Reviewer** (draft SOP -> transitions + flags, one LLM call): names what carries between every
  consecutive step pair, grounded in a verbatim transcript quote the code verifies -- an
  unbridged or ungrounded pair becomes a synthesized structural flag. Separately runs four
  ambiguity checks (unresolved pointer words, missing names, unstated selection rules, unverified
  "automatically" claims) over every step. Degrades instead of failing the run: a failed call sets
  `review_failed=True` and ships the Writer's document with zero flags.
- **Checker** (per flag: frames -> resolution, one LLM call per flag): resolves each flag against
  video frames (OpenAI: real vision, per call) or OCR text (Sarvam: one batched OCR job across all
  checked flags, then a text call per flag). Verdicts: `resolved`, `contradicted`, or
  `unresolvable` -- an `unresolvable` verdict is a real, useful result, not a failure: it means the
  answer lives only in the recorder's head, which becomes a question in the final document rather
  than a guess. A failed call marks that one flag `check_failed`; the run continues.
- **Triage** (pure deterministic function, no LLM call): `report/build_spec.py: triage_flag`
  reduces every flag's `(status, severity)` to `proceed_with_assumption` / `proceed_with_flag` /
  `block_and_escalate`, building the severity-sorted escalation queue.

## Quickstart

1. Install via conda (recommended):
   ```bash
   conda env create -f environment.yml
   conda activate processghost
   ```
   If conda is unavailable:
   ```bash
   pip install -r requirements.txt
   ```
   **Prerequisite: `ffmpeg` and `ffprobe` must both be on PATH** -- the pipeline shells out to
   both directly. Install with `conda install -c conda-forge ffmpeg` (installs both binaries), or
   your OS's package manager. Any test that shells out to either binary is skipped automatically if
   it isn't found.
2. Set environment variables: copy `.env.example` to `.env` and add the key for whichever provider
   you're using. `core/config.py: LLM_PROVIDER` defaults to `"sarvam"` (needs `SARVAM_API_KEY`);
   set it to `"openai"` to use `OPENAI_API_KEY` instead. Only the active provider's key is
   required at runtime.
3. Run the Streamlit UI:
   ```bash
   streamlit run app/streamlit_app.py
   ```
4. Or run from the command line (the screen and workbook stages are optional):
   ```bash
   python -m scripts.run_pipeline ../data/english_hackathon_test.mp4 --provider openai --screen --workbook ../data/ProcessGhost_Bills_Tracker_v4_final.xlsx
   ```
   `--screen` adds the screen timeline, cell trail, Reconciler and shown steps; `--workbook` adds the read-only workbook step
   (see `docs/22-architecture-v3.md`). Score a finished run with `python -m evals.score_findings runs/<run_id>`.
5. Upload an mp4 with narration, run, and download `sop.md` (the four-section document: the
   workflow as spoken, what the pipeline assumed, what's missing, and the questions to ask) and
   `build_spec.json` (the escalation queue).

## Project Layout
```
processghost/
  app/streamlit_app.py        # UI
  core/
    io/                       # video/audio extraction, storage, image encoding
    llm/                      # LLMClient (OpenAI/LangChain) + SarvamClient + prompts + parsing
    screen/                   # screen timeline, surfaces, classes, anchors, cell trail, Reconciler, investigations
    data/                     # workbook profile, read-only tools, facts, answers, claim guard, match check, repeat detection
    slots.py, ladder.py       # the four slots of a data step; the video -> document -> person ladder
    models/sop.py             # the v2 data contract: SOPStep, Flag, StepTransition, Resolution,
                               #   SOPDocument (provenance-validated), and the LLM-facing
                               #   WriterOutput/ReviewerOutput/CheckerOutput schemas
    models/tokens.py          # TranscriptSegment only
    pipeline.py                # run_pipeline: Writer -> Reviewer -> Checker -> Triage
    config.py                  # LLM_PROVIDER, pricing, Checker frame/cap tuning
  report/
    sop_render.py              # deterministic sop.md renderer (four sections, pure function)
    flow.py                    # flow.json and flow.md: the process flow with its gaps drawn in
    build_spec.py              # deterministic build_spec.json renderer + triage_flag
  runs/                        # per-run artifacts (video, frames, sop.json, sop.md,
                                #   build_spec.json, trace.jsonl)
  tests/                       # unit tests (no API key needed) + gated real-video integration tests
```

## Logging and traces
- Every LLM/OCR/transcription call writes one line to `runs/<run_id>/trace.jsonl` immediately
  (crash-safe), tagged with stage (`writer`/`reviewer`/`checker`/`transcribe`/
  `checker_ocr_batch`), token counts, real cost (`core/config.py: PRICING_PER_1M_TOKENS`,
  `WHISPER_COST_PER_MINUTE`, `SARVAM_OCR_COST_PER_PAGE`), and latency.
- `runs/<run_id>/run.json` records the run's metrics and file paths.
- `runs/<run_id>/sop_draft.md` (Writer + Reviewer only, pre-Checker) and `sop.md` (final) are both
  kept, giving a free before/after diff of what the Checker changed.

## Constraints & Defaults
- Dual provider: `core/config.py: LLM_PROVIDER` (`"openai"` or `"sarvam"`, default `"sarvam"`).
  OpenAI uses LangChain's `ChatOpenAI` + `with_structured_output` for every reasoning call and the
  raw SDK for Whisper transcription. Sarvam has no true vision model -- the Checker's OpenAI path
  sends real frames per call, the Sarvam path runs one batched OCR job across all checked flags'
  frames first, then reasons over the extracted text per flag.
- Checker cost control: `MAX_CHECKED_FLAGS` (default 20, open flags processed severity-first;
  anything over the cap is marked `unchecked`, never silently dropped), `CHECKER_FRAME_INTERVAL_S`
  / `CHECKER_MAX_FRAMES_PER_FLAG` (dense per-flag sampling), `CHECKER_MAX_OCR_FRAMES` (Sarvam OCR
  batch cap). The Checker is the dominant cost stage by a wide margin (per-flag vision calls run
  ~$0.04-0.06 vs ~$0.006-0.008 for the Writer's or Reviewer's entire whole-transcript call) --
  `MAX_CHECKED_FLAGS` has never actually bound on any real run so far, see
  `../docs/18-known-gaps-v2.md`.
- Every LLM call site follows the same pattern: try -> on failure, `Metrics.record_failure(stage,
  meta)` -> a defined degradation (see the Writer/Reviewer/Checker summaries above) -- no single
  bad call fails the whole run except the Writer's.
- Scenario-agnostic prompts: no sheet/vendor/column name is ever baked into
  `core/llm/prompts.py`, enforced by a grep guard
  (`tests/test_prompts_scenario_agnostic.py`) against the real scenario's actual answers.

## Development Notes
- Python 3.11+, Pydantic v2.
- `SOPDocument`'s provenance rule (every content line traces to exactly one attribution: `said`,
  `inferred`, or an open flag) is enforced by a raising `@model_validator`, not a soft flag -- an
  invalid document cannot be constructed. See `../docs/15-provenance-and-doc-format.md`.
- Every transition the Reviewer claims must be grounded in a `quote`, a verbatim transcript span
  the code (`core/llm/parsing.py: _quote_is_grounded`) verifies is an actual substring of the
  transcript before trusting it as coverage -- an invented or paraphrased quote is treated exactly
  like an omitted transition. See `../docs/18-known-gaps-v2.md` entry 1 for why omission alone
  (no quote requirement) proved unreliable.
- Artifacts are stored per run for auditability. Nothing is uploaded except the active provider's
  API calls for transcription and the three reasoning stages.
