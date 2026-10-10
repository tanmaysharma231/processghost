"""Live-progress viewer for the v2 pipeline. Shows the uploaded video immediately, then --while
the pipeline actually runs in a background thread-- a growing list of expanders, one per
Writer/Reviewer/Checker call, populated with that call's real output the moment it lands in
runs/<run_id>/trace.jsonl. This is a pure disk-polling design: no changes to run_pipeline's
signature or return contract. What makes it possible is core/llm/client.py: Metrics.record_payload
(and its SarvamClient mirror) writing the actual structured output to the trace, not just
cost/latency metadata -- see docs/18-known-gaps-v2.md.

Supersedes the old "kept intentionally minimal, no UI work before Phase 5" viewer -- that
constraint was for an earlier point in the project.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

# `streamlit run app/streamlit_app.py` does not add the project root to sys.path the way pytest's
# pythonpath=["."] (pyproject.toml) does for the test suite -- `core`/`report` are only importable
# below because this script is launched directly by Streamlit's script runner (which puts this
# file's own directory, not the repo root, on sys.path). Without this, the import below raises
# ModuleNotFoundError regardless of the shell's current working directory when `streamlit run` was
# invoked -- confirmed by actually launching the server, not assumed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from core.config import RUNS_DIR
from core.pipeline import WriterFailedError, run_pipeline

st.set_page_config(page_title="ProcessGhost", page_icon=":ghost:", layout="wide")

st.title("ProcessGhost")
st.caption(
    "Upload a narrated screen recording. Writer -> Reviewer -> Checker -> Triage run in order; "
    "each call's real output appears below as soon as it happens."
)

_STAGE_LABELS = {
    "writer": "Writer",
    "reviewer": "Reviewer",
    "checker": "Checker",
    "checker_ocr_batch": "Checker (OCR batch)",
    "transcribe": "Transcribe",
}


def _run_in_background(video_path: Path, result: dict) -> None:
    """Target for the background thread -- stores the outcome in `result` (a plain dict shared
    with the main thread via closure) rather than returning, since a thread's return value is
    otherwise unreachable."""
    try:
        result["artifacts"] = run_pipeline(video_path)
    except WriterFailedError as exc:
        result["error"] = f"Writer stage failed: {exc}"
    except Exception as exc:  # noqa: BLE001 - surface any failure to the page rather than hang it
        result["error"] = f"Pipeline failed: {exc}"


def _discover_run_dir(started_after: float) -> Path | None:
    """The run's directory is created inside run_pipeline (core/io/storage.py: new_run_dir) before
    it does anything else, but run_pipeline never exposes the path while running -- so find it by
    looking for the newest directory under RUNS_DIR created after this page's own start time,
    rather than adding a callback to run_pipeline's signature."""
    if not RUNS_DIR.exists():
        return None
    candidates = [p for p in RUNS_DIR.iterdir() if p.is_dir() and p.stat().st_mtime >= started_after]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _render_trace_line(event: dict) -> None:
    stage = event.get("stage", "unknown")
    label = _STAGE_LABELS.get(stage, stage)
    kind = event.get("event")

    if kind == "payload":
        payload = event.get("payload", {})
        flag_id = None
        if isinstance(payload, dict):
            flag_id = payload.get("flag_id")
        title = f"{label}: {flag_id}" if flag_id else label
        with st.expander(f"{title} -- output", expanded=False):
            st.json(payload)
    elif kind == "call":
        cost = event.get("cost_usd")
        latency = event.get("latency_ms")
        cost_str = f"${cost:.4f}" if isinstance(cost, (int, float)) else "?"
        latency_str = f"{latency:.0f}ms" if isinstance(latency, (int, float)) else "?"
        st.caption(f"{label}: call finished -- {cost_str}, {latency_str}")
    elif kind == "failure":
        with st.expander(f"{label} -- FAILED", expanded=True):
            st.error(event.get("error", "unknown error"))


uploaded = st.file_uploader("Upload narrated screen recording (mp4)", type=["mp4"])
run_btn = st.button("Run pipeline", disabled=uploaded is None)

if run_btn and uploaded:
    video_bytes = uploaded.read()
    st.video(video_bytes)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
        tmp.write(video_bytes)
        tmp_path = Path(tmp.name)

    result: dict = {}
    thread = threading.Thread(target=_run_in_background, args=(tmp_path, result), daemon=True)
    started_at = time.time()
    thread.start()

    status = st.empty()
    status.info("Starting...")

    run_dir: Path | None = None
    trace_lines_seen = 0

    while thread.is_alive() or run_dir is not None:
        if run_dir is None:
            run_dir = _discover_run_dir(started_at)

        if run_dir is not None:
            trace_path = run_dir / "trace.jsonl"
            if trace_path.exists():
                lines = trace_path.read_text(encoding="utf-8").splitlines()
                for line in lines[trace_lines_seen:]:
                    if not line.strip():
                        continue
                    try:
                        _render_trace_line(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # a partially-flushed final line -- picked up whole next poll
                trace_lines_seen = len(lines)

        if not thread.is_alive():
            break
        status.info("Running...")
        time.sleep(1.5)

    thread.join()

    if "error" in result:
        status.error(result["error"])
    else:
        artifacts = result.get("artifacts")
        if artifacts is None:
            status.error("Pipeline finished with no result and no recorded error.")
        else:
            status.success(f"Run complete: {artifacts.run_id}")

            if artifacts.sop_md:
                st.subheader("Final SOP")
                st.markdown(artifacts.sop_md.read_text(encoding="utf-8"))
                st.download_button(
                    "Download sop.md", artifacts.sop_md.read_bytes(), file_name="sop.md"
                )

            if artifacts.build_spec_path:
                st.subheader("Escalation queue")
                spec = json.loads(artifacts.build_spec_path.read_text(encoding="utf-8"))
                queue = spec.get("escalation_queue", [])
                if queue:
                    st.json(queue)
                else:
                    st.caption("Nothing escalated -- no blocking, unresolved flag this run.")
                st.download_button(
                    "Download build_spec.json",
                    artifacts.build_spec_path.read_bytes(),
                    file_name="build_spec.json",
                )

            with st.expander("Run metrics / fallbacks"):
                st.json(artifacts.metrics.get("fallbacks", []))
