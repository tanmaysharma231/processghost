# ProcessGhost

Compare what an expert **says** (transcript) vs what they **do** (screen actions) in a narrated
screen recording and flag the gaps -- the expertise blindness -- before writing documentation or
automating the process.

This implementation is built on the working pipeline infrastructure from the `process-integrity`
hackathon project (real ffmpeg frame extraction, real Whisper transcription, a deterministic
alignment + rule engine, Streamlit UI), with the intent-extraction and action-description stages
now wired to real OpenAI chat/vision calls instead of heuristics. See
`../docs/00-README.md` for the full ProcessGhost concept and design docs, and
`../docs/06-known-gaps.md` for what the original Next.js/Lyzr reference app got wrong that this
project is meant to fix.

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
   Install ffmpeg if missing: `conda install -c conda-forge ffmpeg`.
2. Set environment variables: copy `.env.example` to `.env` and add your `OPENAI_API_KEY`.
3. Run Streamlit UI:
   ```bash
   streamlit run app/streamlit_app.py
   ```
4. Upload an mp4 with narration, tweak sampling (frame interval), run, and download the gap report.

## Project Layout
```
processghost/
  app/streamlit_app.py        # UI
  core/                       # pipeline components
    io/                       # video/audio extraction, storage, image encoding
    llm/                      # OpenAI client + prompts
    models/                   # Pydantic schemas
    align/                    # intent/action alignment
    rules/                    # rule engine + dissonance rules
  report/render.py            # report generation (md/html)
  runs/                       # per-run artifacts (video, frames, json, reports)
  logs/                       # workflow + change logs + run metrics
  tests/                      # unit tests (no API key needed) + gated integration test
```

## Logging
- `logs/workflow.log`: run-time events for the current workflow.
- `logs/change-log.md`: append-only log of design/code changes with timestamps.
- `logs/run-metrics.csv`: per-run token/cost/time metrics (OpenAI calls) -- now includes real
  chat/vision call costs, not just Whisper.
- Each run also writes `runs/<run_id>/run.json` with metrics and file paths.

## Constraints & Defaults
- OpenAI only: Whisper for transcription, `gpt-4o` for intent extraction, `gpt-4o-mini` (vision)
  for action description. See `.env.example` for the model env vars.
- Vision calls are capped per run (`core/config.py: MAX_VISION_CALLS_PER_RUN`) -- if more frames
  are extracted than the cap, they're evenly subsampled before any vision call is made, to keep
  cost and latency bounded.
- ROI: auto presets only (center, center+top_bar). Manual ROI hooks reserved but not exposed in UI
  yet -- real crop application is a documented next step, not implemented here.
- Rule set: unexplained action, silent step, vague intent, teleportation, optional contradiction.
- If an intent-extraction or action-description call fails or returns malformed JSON, that single
  item falls back to the previous heuristic and the failure is recorded in the run's metrics --
  one bad call does not fail the whole run.

## What's real vs. not yet built
- Real: audio extraction, frame extraction, transcription, intent extraction (LLM), action
  description (vision LLM), deterministic alignment, the 5-rule dissonance engine, report
  rendering, per-run persistence, Streamlit UI.
- Not yet built (see `../docs/11-open-questions.md` and the plan history): SOP synthesis into a
  full annotated document, an automatic judge/critique loop, real crop-region application, an
  LLM-based alignment judge (the deterministic keyword+time scorer is what actually runs).

## Development Notes
- Python 3.11+, Pydantic v2.
- Deterministic alignment first (keywords + time proximity); LLM judge is optional and guarded
  (still unused -- see above).
- Artifacts are stored per run for auditability. Nothing is uploaded except OpenAI API calls for
  transcription, intent extraction, and action description.
