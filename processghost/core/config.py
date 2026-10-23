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
# fully verified path, real vision per Checker call. "anthropic": ChatAnthropic (ANTHROPIC_MODEL_*
# below); transcription stays on OpenAI Whisper either way. Requires OPENAI_API_KEY (and
# ANTHROPIC_API_KEY for "anthropic").
LLM_PROVIDER: Literal["openai", "anthropic"] = "openai"

# Models -- Anthropic provider (core/llm/client.py). Reasoning is on Claude; transcription still
# uses OpenAI Whisper because Anthropic has no speech-to-text. UNVERIFIED against the live API: built on the
# installed langchain-anthropic and tested with fakes only. Sampling settings are not sent (the newest
# models reject non-default temperature), so LLM_TEMPERATURE does not apply to this provider.
ANTHROPIC_MODEL_TEXT = "claude-sonnet-5"
ANTHROPIC_MODEL_VISION = "claude-sonnet-5"
ANTHROPIC_MAX_TOKENS: int = 16000

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
# Split one run of activity into two events when, after a still sample, the next change happens
# somewhere that does not touch the run's earlier changes (two quick actions in different places).
# OFF by default: it contradicts "changes inside the settle window are one event" (tests/test_screen_timeline.py)
# and has not been measured on a real recording. Turn on per run to compare.
SCREEN_SPLIT_ON_LOCUS_SHIFT: bool = False
SCREEN_LOCUS_MARGIN_PX: int = 24  # boxes closer than this (original pixels) count as the same locus
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
SCREEN_EVIDENCE_MAX_BOXES: int = 4  # region groups tiled per event (merged by proximity, never dropped by size)
SCREEN_EVIDENCE_MAX_IMAGES: int = 6  # hard cap on images in one Checker call
SCREEN_ESCALATION_EVENTS: int = 2  # level 1 widens to this many events either side of the nearest
# A "resolved" verdict whose observation shares at least this fraction of its content words with the
# prompt is downgraded to unresolvable (docs/18 entry 5). Untuned; measured replay means were 0.34-0.54.
CHECKER_ECHO_MAX: float = 0.8

# --- Reconciler (docs/24) ---
SCREEN_RECORD_CROP_PAD_PX: int = 20  # padding around a changed box before OCR'ing it for the change record
SCREEN_RECORD_MAX_REGIONS: int = 3  # region GROUPS recorded per event; boxes are merged by proximity, never dropped by size (ADR 0010)
SCREEN_REGION_MAX_CROP_FRAC: float = 0.5  # a merged group larger than this share of the frame is split back (untuned)
SCREEN_RECORD_MAX_CHARS: int = 500  # each OCR field is truncated to this before it is sent to the model
RECONCILER_MAX_QUESTIONS_PER_STEP: int = 3

# --- Surface locators and context map (ADR 0013). All untuned starting guesses. ---
SURFACE_MIN_CONFIDENCE: float = 0.4  # a profile must score at least this to be trusted; else the generic fallback
# Narration lagged actions by 4-5 s on the one measured clip (docs/24), so the window reaches far AFTER the
# event and only a little before it (a speaker announcing the move in advance).
SURFACE_SPEECH_WINDOW_BEFORE_S: float = 2.0
SURFACE_SPEECH_WINDOW_AFTER_S: float = 5.0
SURFACE_SPEECH_MIN_RATIO: float = 0.6  # difflib ratio for a spoken word to count as naming a locator token
CONTEXT_MAX_OCR_CHARS: int = 800  # cap on the labels read from a context's first full frame

# --- Event classes, selection anchors and reading fallback (docs/26 flow plan). All untuned starting guesses. ---
SCREEN_TEXT_CHANGE_MIN_RATIO: float = 0.9  # before/after text with a difflib ratio at or above this is "the same" (OCR jitter)
ANCHOR_VALUE_WINDOW_S: float = 20.0  # a later data change this soon after a selection can be its paste target
ANCHOR_POINTER_WORDS: tuple = ("this", "here", "that", "these", "there")  # English only for now
OCR_RETRY_VARIANTS: int = 3  # re-reads of a failed field with a changed input: upscale, contrast, wider padding
VLM_READ_MAX_CROPS: int = 12  # failed crops sent to the cheap vision model per run; over the cap they stay unread

# --- Step slots (docs/26 flow plan) ---
SLOT_SHOWN_WINDOW_S: float = 1.0  # a step known only from the screen has no spoken window; its flags use [t, t + this]

# --- Document rung: read-only workbook access (docs/26 flow plan). All untuned starting guesses. ---
DATA_ROW_CAP: int = 25  # rows one tool call may return
DATA_CELL_MAX_CHARS: int = 200  # a cell string is cut to this before it reaches the model
DATA_DISTINCT_MAX: int = 12  # a column with at most this many distinct values lists them all in the profile
DATA_MAX_CALLS_PER_FLAG: int = 4  # tool rounds per question; one more round is allowed, but only to give a verdict
DATA_ROUTE_NON_SLOT: bool = False  # also send questions that are not one of a data step's four slots to the model loop
DATA_MAX_ROUTED_FLAGS: int = 12  # questions sent to the document rung per run
DATA_PASS_BUDGET_USD: float = 0.30  # estimated spend on the document rung; the rest are skipped, never silently dropped
DATA_MAX_WORKBOOK_BYTES: int = 20 * 1024 * 1024

# --- Retrieval library inspection budget (ADR 0012). Starting guesses; a cache hit costs nothing. ---
INSPECT_MAX_IMAGES: int = 12  # full frames, comparisons and keyframes handed out per retriever
INSPECT_MAX_CROPS: int = 8
INSPECT_MAX_EXPANSIONS: int = 3  # expand_interval calls
INSPECT_MAX_RETRIES_PER_EPISODE: int = 2  # re-inspections of the same interval at a different detail level

# --- Pre-meeting intake ------------------------------------------------------
# Ingestion limits for docs/19-pre-meeting-intake.md's stage-1 source reading (core/io/documents.py).
# Every truncation here must record an ingest_note rather than silently dropping content -- same
# discipline as MAX_CHECKED_FLAGS marking the overflow "unchecked" instead of just dropping it.
INTAKE_ALLOWED_SUFFIXES: tuple = (".txt", ".md", ".xlsx")
INTAKE_MAX_ROWS_PER_SHEET: int = 200
INTAKE_MAX_CELL_CHARS: int = 200

# --- Cost estimates ----------------------------------------------------------
# $ per 1M tokens, (prompt, completion). OpenAI sourced 2026-08-29 from
# https://www.cloudzero.com/blog/openai-pricing/ (official pricing page figures); re-verify before
# trusting this for real budgeting. A model not in this table costs $0.0 with meta={"pricing": "unknown"}
# (core/llm/client.py: estimate_cost_usd) rather than raising or silently looking free.
PRICING_PER_1M_TOKENS: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    # Anthropic, from the Claude API reference cached 2026-10-06 (Haiku 5.5 is for prompts up to 100K tokens).
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
# Non-token-based costs, priced by their own real unit -- forcing these into the token table above
# would silently misprice them.
WHISPER_COST_PER_MINUTE: float = 0.006

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
# Content-addressed model-call cache (core/llm/cache.py): a testing aid. Off by default; enable per run
# with scripts/run_pipeline.py --cache. Only the stages in CACHE_STAGES are ever cached -- the
# expensive image/document/transcription calls. The Writer, Reviewer and Reconciler always call the
# model, so their run-to-run variation stays measurable. The directory is safe to delete at any time.
CACHE_ENABLED: bool = False
CACHE_STAGES: frozenset = frozenset({"transcribe", "checker", "document", "read_fallback"})
CACHE_DIR = ROOT / "cache"

# Input and spend limits (core/guards.py, core/pipeline.py). Tight on purpose: the pipeline is only
# validated on recordings of tens of seconds, and cost grows with length.
MAX_VIDEO_BYTES: int = 500 * 1024 * 1024
MAX_VIDEO_DURATION_S: float = 900.0
# Soft budget for ONE run. Once estimated spend passes it the Checker stops starting new flags and
# marks the rest "unchecked" (never silently dropped). Writer/Reviewer are not interrupted. Cache hits
# cost nothing. Set to 0 to disable.
MAX_RUN_COST_USD: float = 2.0
# Per-request timeout for provider calls (seconds); without it a stalled connection hangs the run.
LLM_TIMEOUT_S: float = 120.0
# Keep the video copy, audio and frames of each run on disk. Set False (scripts/run_pipeline.py
# --no-keep-media) to delete them when the run ends; see docs/data-handling.md and core/retention.py.
KEEP_RUN_MEDIA: bool = True

# Logging
WORKFLOW_LOG_PATH = LOGS_DIR / "workflow.log"
CHANGE_LOG_PATH = LOGS_DIR / "change-log.md"
RUN_METRICS_PATH = LOGS_DIR / "run-metrics.csv"
