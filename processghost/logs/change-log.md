# Change Log (append-only)

- 2026-07-26: Seeded `processghost/` from `AI_ENGINEER_DAY_HACKATHON/process-integrity/`
  (real ffmpeg frame extraction, real Whisper transcription, Pydantic models, deterministic
  alignment + 5-rule dissonance engine, report renderer, Streamlit UI). `runs/`/`logs/` recreated
  empty rather than copied. Rebranded README/app title to ProcessGhost. Author: Claude (session).
- 2026-07-26: Wired real LLM/vision calls into the pipeline, replacing the two heuristic stubs:
  - `core/llm/prompts.py`: replaced one-line stub prompts with full JSON-mode instructions for
    intent extraction and action description, matching `IntentToken`/`ActionToken` fields exactly.
  - `core/llm/parsing.py` (new): parses OpenAI JSON-mode responses into `IntentToken`/`ActionToken`,
    raising `LLMResponseParseError` on anything malformed. Independently unit-tested with canned
    JSON, no API key needed.
  - `core/io/images.py` (new): `frame_to_data_uri` -- base64-encodes an extracted frame for a
    vision message.
  - `core/io/video.py`: added `subsample_evenly` -- caps how many extracted frames get a real
    vision call per run (cost/latency guard; full scene-change sampling stays a documented future
    step, see `../docs/09-visual-gap-filler.md`).
  - `core/config.py`: added `MAX_VISION_CALLS_PER_RUN` (default 20).
  - `core/llm/client.py`: `Metrics` gained `record_failure`/`failures` so every fallback-to-
    heuristic is recorded on the run rather than happening silently.
  - `core/pipeline.py`: `_intent_from_segment` and `_action_from_frame` now make real
    `client.chat()`/`client.vision()` calls with JSON-mode structured output, each falling back to
    its previous heuristic (and recording why) on any exception or unparseable response. Frames
    outside the per-run vision budget also use the heuristic. Everything downstream (alignment,
    rules, report, storage, Streamlit) is unchanged.
  - `tests/` (new): unit tests for parsing, fallback behavior, and subsampling (29 tests, no API
    key required); one integration test gated on `OPENAI_API_KEY` + a fixture video, currently
    skipped since neither exists yet in this repo (`../docs/11-open-questions.md` #6).
  - Deferred, not built in this phase: SOP synthesis into ProcessGhost's `SopDocument` shape, the
    automatic judge/critique loop, real crop-region application, an LLM-based alignment judge.
  Author: Claude (session).
- 2026-07-26: Real data added at `Process_ghost/data/` (`english_hackathon_test.mp4` +
  `ProcessGhost_Bills_Tracker_v4_final.xlsx`), matching the scenario documented since before real
  files existed. Verified end to end: ffmpeg installed via a dedicated `processghost` conda env,
  full pipeline run against the real video, real cost measured (~$0.076/run at full frame
  resolution). Added `VISION_MAX_DIMENSION` frame downscaling (`core/io/images.py`,
  `core/config.py`) after confirming ~30% real token savings on a single-frame check with no
  observed accuracy loss. Wrote `docs/12-real-scenario-target.md` -- the real transcript, real
  workbook structure, and the six specific gaps a correct output must surface, as a validation
  fixture (never a spec to encode into a prompt). Initialized git at `Process_ghost/` (previously
  untracked). Author: Claude (session).
- 2026-07-26: Diagnosed why only 1 of the 6 documented gaps was caught (arbitrary `>=3 actions`
  clustering threshold in the old `r_vague_intent`; no rule existed at all for "a claimed outcome
  nothing observed confirms," the mirror of `r_unexplained`). Rebuilt gap-detection to generalize
  rather than curve-fit to this one video:
  - `core/llm/prompts.py`: replaced per-segment `INTENT_EXTRACTION_SYSTEM` with
    `TRANSCRIPT_EXTRACTION_SYSTEM` -- ONE call for the whole transcript at once (not one per
    segment), extracting `entities_referenced[]` (with `named_specifically`), `selection_criteria`,
    and `claimed_outcome` per line, so the model can cross-reference a reference used repeatedly
    and never resolved, or a claim nothing ever backs up, across the entire recording. Added
    `CROSS_CHECK_SYSTEM` -- ONE further call, over compact text summaries (not images), comparing
    extracted entities/claims against observed actions to find unresolved entities and unconfirmed
    claims across the whole run. Removed the never-wired-up `ALIGNMENT_JUDGE_SYSTEM` (superseded).
  - `core/models/tokens.py`: `IntentToken` gained `entities_referenced`, `selection_criteria`,
    `claimed_outcome`; new `EntityReference`, `UnresolvedEntityFinding`, `UnconfirmedClaimFinding`
    models; `RunArtifacts` gained `unresolved_entities`/`unconfirmed_claims`.
  - `core/llm/parsing.py`: `parse_intent_response` replaced by
    `parse_transcript_extraction_response` (batch, all-or-nothing per call); added
    `parse_cross_check_response`.
  - `core/pipeline.py`: `_intent_from_segment` (per-segment loop) replaced by
    `_extract_intents_from_transcript` (one call); added `_cross_check_resolution` (one call).
    Text-side LLM call count is now constant at 2 per run regardless of transcript length (was N).
  - Deleted `core/rules/r_vague_intent.py` (arbitrary threshold, superseded). Added
    `core/rules/r_unresolved_reference.py` and `core/rules/r_unconfirmed_outcome.py` -- both thin
    wrappers over the cross-check call's pre-computed findings.
  - `tests/test_rules.py` (new): covers both new rules plus one synthetic second-domain scenario
    (a support-ticket workflow, unrelated to spreadsheets) as an anti-overfitting check.
  - Verified: 58 unit tests pass (no API key needed); grep guard
    (`grep -riE "bill.?id|master bills|sumif|\bpaid\b|aws" core/`) returns nothing -- no
    scenario-specific strings anywhere in the new/changed code; one real run against
    `data/english_hackathon_test.mp4` now catches 4 of 6 documented gaps outright (all three
    unnamed sheets, plus the Status="Paid" claim correctly flagged as unconfirmed) plus two
    previously-undocumented vague-pronoun catches ("it", "here"), up from 1 of 6 before, at
    ~$0.067/run (2 gpt-4o calls + 12 gpt-4o-mini vision calls + 1 Whisper call, zero fallbacks).
  Author: Claude (session).
