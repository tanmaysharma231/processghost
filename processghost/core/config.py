"""Central configuration defaults for Process Integrity."""
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
LOGS_DIR = ROOT / "logs"

# Alignment thresholds
MATCH_WINDOW_S: float = 10.0
TAU_LOW: float = 0.25
TAU_HIGH: float = 0.65

# Sampling defaults
FRAME_INTERVAL_S: float = 2.0
INTENT_PADDING_S: float = 5.0

# ROI presets
ROI_PRESETS: dict[str, dict[str, float]] = {
    "center": {"x1": 0.15, "y1": 0.15, "x2": 0.85, "y2": 0.85},
    "center_top_bar": {"x1": 0.15, "y1": 0.0, "x2": 0.85, "y2": 0.9},
}
DEFAULT_ROI: Literal["center_top_bar"] = "center_top_bar"

# Models
MODEL_TEXT = "gpt-4o"
MODEL_VISION = "gpt-4o-mini"
MODEL_WHISPER = "whisper-1"

# Cost/latency guard: cap how many extracted frames get a real vision call per run. If more frames
# are extracted than this, they are evenly subsampled first (core.io.video.subsample_evenly).
MAX_VISION_CALLS_PER_RUN: int = 20

# Cost guard: downscale a frame's longer side to at most this many pixels before sending it to the
# vision model (core.io.images.frame_to_data_uri). Vision token cost scales with pixel dimensions,
# not file size -- a real run against a 1920x1080 recording showed ~37k prompt tokens per frame at
# full resolution (~90% of that run's total cost). 1024 balances cost against keeping on-screen
# text legible.
VISION_MAX_DIMENSION: int = 1024

# Logging
WORKFLOW_LOG_PATH = LOGS_DIR / "workflow.log"
CHANGE_LOG_PATH = LOGS_DIR / "change-log.md"
RUN_METRICS_PATH = LOGS_DIR / "run-metrics.csv"

# Rule toggles
ENABLE_CONTRADICTION = True
