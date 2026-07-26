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
