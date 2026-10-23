# Data handling

Recordings of someone's screen can contain client data, personal data and credentials. This is what
ProcessGhost stores, what leaves the machine, and what it does **not** do. Read it before running the
pipeline on a real client recording.

## What stays on this machine, and where

| What | Where | Contains |
|---|---|---|
| Copy of the input video | `processghost/runs/<run_id>/video.mp4` | The full recording |
| Extracted audio | `runs/<run_id>/audio.wav` | The full narration |
| Frames sent to the Checker | `runs/<run_id>/checker_frames/` | Screenshots (v2 path, and the v3 fallback) |
| Screen timeline | `runs/<run_id>/screen/` | Full-resolution before/after keyframes of every change |
| Evidence images | `runs/<run_id>/checker_frames/events/` | Downscaled full frames and crops sent to the model |
| Outputs | `runs/<run_id>/sop.md`, `sop.json`, `build_spec.json`, `run.json` | The transcript, steps, and anything the models quoted from the screen |
| Call trace | `runs/<run_id>/trace.jsonl` | Every model answer verbatim, including text read from the screen and every workbook lookup |
| Workbook copy and profile | `runs/<run_id>/workbook/`, `workbook_profile.json` | A copy of the workbook you supplied, and a profile of it (with `--workbook` only) |
| Manifest | `runs/<run_id>/manifest.json` | Code version, prompt hashes, settings, input hash. No keys. |
| Model-call cache | `processghost/cache/` | Model answers and the transcript, keyed by hash. **Unencrypted.** |
| Run summary | `processghost/logs/run-metrics.csv` | One line per run: name, tokens, cost |

## What leaves the machine

Sent to the model provider you configure (OpenAI by default, Anthropic optionally; the audio always goes to OpenAI Whisper):

- the audio (to speech-to-text);
- the transcript, the draft steps and the flag questions (to the Writer, Reviewer and Checker);
- OCR **text** of the screen (OCR itself runs locally), and images (a downscaled full frame and
  crops of what changed) only on the Checker's second, escalation call.

- with `--workbook`: lookups into the workbook you supply. Only what a lookup returns is sent (at most
  `DATA_ROW_CAP` rows per call, each cell cut to `DATA_CELL_MAX_CHARS`, and only the columns asked for),
  plus a profile of the workbook (sheet names, headers, a few sample values per column, every distinct
  value of low-cardinality columns, formulas as written). **Cell text is data from your file and can be
  client data.** The workbook is opened as a copy inside the run directory (`runs/<run_id>/workbook/`);
  the original is never written to and its hash is checked at the end of the run.
- when OCR cannot read a selected-cell field after re-reading it (`core/screen/read_fallback.py`): a small
  cropped strip of the top of the window (name box and formula bar), at most `VLM_READ_MAX_CROPS` per
  run, to the vision model configured as `MODEL_VISION`, in one batched call.

Not sent anywhere: the screen timeline computation (OpenCV) and the OCR engine (RapidOCR) run locally.
So do the surface locators, the context map, the investigation index, the retrieval library and the
dry-run script (`docs/26-adaptive-visual-evidence.md`): they read pixels and OCR text on this machine and
make no provider call. A hosted vision reading for unrecognised screens was considered and is NOT built;
if it is added it will send downscaled screen images to the provider and this page must say so first.

Check your provider's data-retention and training terms for the account you use. Free tiers in
particular may use requests for training; do not use them for client recordings.

## What ProcessGhost does not do

- No redaction. Names, numbers and credentials visible on screen or spoken are sent as they are.
- No detection of personal data.
- No encryption at rest for runs or the cache.
- No access control: anyone who can read the folder can read everything above.
- No guarantee about the provider's handling once data is sent.

## Controls that exist

- `--no-keep-media` (`scripts/run_pipeline.py`, or `config.KEEP_RUN_MEDIA = False`): at the end of
  the run, delete the video copy, audio and every frame directory. Text outputs stay; they still
  contain the transcript and quoted screen text.
- `--no-cache`: neither read nor write the model-call cache (also what an evaluation of run-to-run
  variance must use, otherwise it replays the first answer).
- `python -m scripts.cleanup --older-than-days N [--media-only] [--include-cache] [--yes]`: delete old
  runs, strip media from them, or clear old cache entries. It is a dry run unless `--yes` is given.
- Input limits (`config.MAX_VIDEO_BYTES`, `MAX_VIDEO_DURATION_S`) and a soft per-run budget
  (`MAX_RUN_COST_USD`) are enforced; see `docs/scope-and-limits.md`.
- API keys are read from the environment or a git-ignored `.env`; a test checks the manifest does not
  contain them. Traces record model answers, not requests, so keys do not appear there either.

## Suggested practice for client recordings

1. Run with `--no-keep-media`, and clear the cache (`--include-cache`) when the engagement ends.
2. Put `runs/` and `cache/` on an encrypted volume and restrict access to it.
3. Review `sop.md` and `trace.jsonl` before sharing: both can contain on-screen text.
4. Schedule `scripts.cleanup` so retention is not left to memory.
