"""Central configuration defaults for ProcessGhost."""
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
LOGS_DIR = ROOT / "logs"

# Sampling defaults
# This sparse uniform pass only serves Checker lead-ins and the doc appendix -- dense per-flag
# extraction happens on demand via core.io.video.extract_frames_window.
FRAME_INTERVAL_S: float = 4.0

# Models -- OpenAI provider
MODEL_TEXT = "gpt-4o"
MODEL_VISION = "gpt-4o-mini"
MODEL_WHISPER = "whisper-1"

# Cost guard: downscale a frame's longer side to at most this many pixels before sending it to the
# vision model (core.io.images.frame_to_data_uri). Vision token cost scales with pixel dimensions,
# not file size -- a real run against a 1920x1080 recording showed ~37k prompt tokens per frame at
# full resolution (~90% of that run's total cost). 1024 balances cost against keeping on-screen
# text legible.
VISION_MAX_DIMENSION: int = 1024

# --- Provider switch --------------------------------------------------------
# "openai": Whisper + LangChain's ChatOpenAI (gpt-4o text, gpt-4o-mini vision) -- the original,
# fully verified path, real vision per Checker call. "sarvam": Saaras v3 (batch STT) + Sarvam-105B
# + Sarvam document-intelligence OCR (no true vision model exists on Sarvam -- see
# core/llm/sarvam_client.py and core/pipeline.py for the OCR-extract-then-text-reason design this
# implies). Requires SARVAM_API_KEY.
LLM_PROVIDER: Literal["openai", "sarvam"] = "sarvam"

# Models -- Sarvam provider
SARVAM_MODEL_CHAT = "sarvam-105b"
SARVAM_MODEL_STT = "saaras:v3"
SARVAM_MODEL_TTS = "bulbul:v3"
SARVAM_TTS_SPEAKER = "shubh"
SARVAM_TTS_LANGUAGE_CODE = "en-IN"
SARVAM_DOC_INTELLIGENCE_LANGUAGE = "en-IN"

# --- Checker -----------------------------------------------------------------
# Run-level cap on how many flags actually get a Checker call (processed in severity order,
# blocking first) plus the dense per-flag windowed-extraction parameters. See core/pipeline.py:
# _run_checker and core/io/video.py's extract_frames_window.
MAX_CHECKED_FLAGS: int = 20
CHECKER_FRAME_INTERVAL_S: float = 0.5
CHECKER_LEAD_IN_S: float = 3.0
CHECKER_MIN_WINDOW_S: float = 4.0
CHECKER_MAX_FRAMES_PER_FLAG: int = 16
CHECKER_FALLBACK_WINDOW_S: float = 30.0
# Page budget for the ONE batched Sarvam document-intelligence job covering every checked flag's
# frames per run -- see SarvamClient.ocr_extract_frames / core/pipeline.py: _run_checker.
CHECKER_MAX_OCR_FRAMES: int = 60

# --- Screen timeline ---------------------------------------------------------
# Pixel-diff parameters for core/screen/diff.py (docs/22 stage 0b). All distances/areas are in the
# DOWNSCALED diff frame (width SCREEN_DIFF_WIDTH), not the original video resolution.
SCREEN_DIFF_WIDTH: int = 960
SCREEN_BLUR_KSIZE: int = 5  # odd; suppresses compression noise before differencing
SCREEN_PIXEL_THRESH: int = 25  # 0-255 grayscale delta that counts as a changed pixel
SCREEN_DILATE_PX: int = 9  # merges nearby changed pixels (e.g. the glyphs of one cell) into one box
SCREEN_MIN_BOX_AREA: int = 150  # boxes smaller than this (cursor, 1-px jitter) are dropped
SCREEN_NO_CHANGE_FRAC: float = 0.0005  # changed fraction below this == "identical frames"
# Timeline (core/screen/timeline.py). Classification does NOT use changed-pixel fraction alone: a
# spreadsheet sheet switch changes only ~10% of pixels (docs/23-checker-learnings.md), so
# "scene_cut" is decided by how much of the frame the changed region spans, not how many pixels moved.
SCREEN_FPS: float = 3.0  # samples per second; screens do not change faster than this in practice
# A sample counts as "something changed" only above this changed-pixel fraction. Compression and
# cursor/caret flicker sit around 0.0001-0.0002 on the one recording measured so far (see
# docs/23-checker-learnings.md); real cell edits and page switches were 0.001-0.11. Tuned on ONE
# clip -- re-measure on every new recording (scripts/screen_timeline.py prints the activity trace).
SCREEN_ACTIVE_FRAC: float = 0.0004
SCREEN_SETTLE_S: float = 1.0  # an event ends after this long with no change
SCREEN_SCENE_COVERAGE: float = 0.30  # changed-region bounding box >= this share of the frame == context switch
SCREEN_SCENE_MIN_CHANGED: float = 0.03  # ...and at least this fraction of pixels must have changed
SCREEN_SCROLL_MIN_SHIFT_PX: float = 8.0  # vertical shift (diff-frame px) that counts as a scroll candidate
SCREEN_SCROLL_MIN_RESPONSE: float = 0.3  # phase-correlation confidence required to call it a scroll
SCREEN_TOP_STRIP_PCT: float = 0.12  # OCR'd for window/page/toolbar text when labelling a scene
SCREEN_BOTTOM_STRIP_PCT: float = 0.15  # OCR'd for tab/status-bar text (sheet or page names live here)
# (x, y, w, h) rectangles in original pixels that never count as change (meeting thumbnails, clock).
SCREEN_MASK_RECTS: tuple = ()

# --- Align (docs/22 stage 2b) ---
# Narration usually lags the action (the copy is spoken ~2 s after it happens), so an event counts as
# belonging to a step if it falls inside the step's spoken span or up to this long BEFORE it.
SCREEN_ALIGN_LEAD_S: float = 3.0
# A step's spoken span may hold this many screen events before the extras are treated as silent-step
# candidates. Untuned: set from one clip, to be measured against labelled cases (evals/).
SCREEN_ALIGN_MAX_EVENTS_PER_STEP: int = 2

# --- Checker, event evidence (docs/22 stage 3) ---
SCREEN_EVIDENCE_FULL_WIDTH: int = 1024  # full after-frame is downscaled to this width: context, not detail
SCREEN_EVIDENCE_BEFORE_WIDTH: int = 512  # low-res before-frame, sent for context switches only
SCREEN_CROP_PAD_PX: int = 60  # padding (original pixels) around each changed box, to catch nearby headers
SCREEN_EVIDENCE_MAX_BOXES: int = 4  # largest changed boxes tiled per event
SCREEN_EVIDENCE_MAX_IMAGES: int = 6  # hard cap on images in one Checker call
SCREEN_ESCALATION_EVENTS: int = 2  # level 1 widens to this many events either side of the nearest
# A "resolved" verdict whose observation shares at least this fraction of its content words with the
# prompt is downgraded to unresolvable (docs/18 entry 5). Untuned; measured replay means were 0.34-0.54.
CHECKER_ECHO_MAX: float = 0.8

# --- Pre-meeting intake ------------------------------------------------------
# Ingestion limits for docs/19-pre-meeting-intake.md's stage-1 source reading (core/io/documents.py).
# Every truncation here must record an ingest_note rather than silently dropping content -- same
# discipline as MAX_CHECKED_FLAGS marking the overflow "unchecked" instead of just dropping it.
INTAKE_ALLOWED_SUFFIXES: tuple = (".txt", ".md", ".xlsx")
INTAKE_MAX_ROWS_PER_SHEET: int = 200
INTAKE_MAX_CELL_CHARS: int = 200

# --- Cost estimates ----------------------------------------------------------
# $ per 1M tokens, (prompt, completion). Sourced 2026-08-29:
# OpenAI: https://www.cloudzero.com/blog/openai-pricing/ (official pricing page figures).
# Sarvam: third-party aggregators (artificialanalysis.ai, sarvam.ai/api-pricing) -- Sarvam's own
# docs are unreliable (see core/llm/sarvam_client.py's module docstring); re-verify before trusting
# this for real budgeting. A model not in this table costs $0.0 with meta={"pricing": "unknown"}
# (core/llm/client.py: estimate_cost_usd) rather than raising or silently looking free.
PRICING_PER_1M_TOKENS: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "sarvam-105b": (0.04, 0.17),
}
# Non-token-based costs, priced by their own real unit -- forcing these into the token table above
# would silently misprice them.
WHISPER_COST_PER_MINUTE: float = 0.006
SARVAM_OCR_COST_PER_PAGE: float = 0.006  # ~INR 0.50/page at time of sourcing above

# --- Reliability -------------------------------------------------------------
# Retry policy (core/llm/retry.py): transient errors only (rate limit, timeout, connection, 5xx),
# exponential backoff with jitter; a schema-mismatch response is re-asked once.
RETRY_MAX_ATTEMPTS: int = 5
RETRY_MAX_PARSE_ATTEMPTS: int = 2
RETRY_INITIAL_WAIT_S: float = 2.0
RETRY_MAX_WAIT_S: float = 60.0
# Sampling: pinned (not left to the provider default) so a rerun differs only by model nondeterminism
# that temperature 0 cannot remove, and so it is part of every cache key and run manifest.
LLM_TEMPERATURE: float = 0.0
# Content-addressed model-call cache (core/llm/cache.py). Disable per run with scripts/run_pipeline.py
# --no-cache; the directory is safe to delete at any time.
CACHE_ENABLED: bool = True
CACHE_DIR = ROOT / "cache"

# Logging
WORKFLOW_LOG_PATH = LOGS_DIR / "workflow.log"
CHANGE_LOG_PATH = LOGS_DIR / "change-log.md"
RUN_METRICS_PATH = LOGS_DIR / "run-metrics.csv"
