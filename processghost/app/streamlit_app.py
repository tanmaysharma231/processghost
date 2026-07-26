import streamlit as st
from pathlib import Path
import tempfile

from core import config
from core.pipeline import run_pipeline


st.set_page_config(page_title="ProcessGhost", page_icon=":ghost:", layout="wide")

st.title("ProcessGhost -- Gap Checker")
st.caption("Compare what experts say vs do. Hosted Whisper + vision optional. MVP focuses on detection, not generation.")

uploaded = st.file_uploader("Upload narrated screen recording (mp4)", type=["mp4"])
frame_interval = st.slider("Frame interval (seconds)", min_value=1.0, max_value=5.0, value=config.FRAME_INTERVAL_S, step=0.5)

run_btn = st.button("Run Gap Check", disabled=uploaded is None)

if run_btn and uploaded:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
        tmp.write(uploaded.read())
        tmp_path = Path(tmp.name)

    with st.spinner("Running pipeline..."):
        artifacts = run_pipeline(tmp_path, frame_interval_s=frame_interval)

    st.success(f"Run complete: {artifacts.run_id}")
    st.metric("Dissonances", len(artifacts.dissonances))
    st.metric("Actions", len(artifacts.actions))
    st.metric("Intents", len(artifacts.intents))

    for d in artifacts.dissonances:
        st.subheader(f"{d.kind} -- {d.title}")
        st.write(f"Severity: **{d.severity}**  |  t={d.t_s:.1f}s")
        st.write(d.explanation)
        if d.evidence.frame_paths:
            for fp in d.evidence.frame_paths:
                try:
                    st.image(str(artifacts.frames_dir / Path(fp).name))
                except Exception:
                    pass
        if d.evidence.transcript_excerpt:
            st.code(d.evidence.transcript_excerpt)

    if artifacts.report_md:
        st.download_button("Download report.md", artifacts.report_md.read_bytes(), file_name="report.md")
    if artifacts.report_html:
        st.download_button("Download report.html", artifacts.report_html.read_bytes(), file_name="report.html")
