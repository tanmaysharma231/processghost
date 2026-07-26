"""Report rendering (markdown + simple HTML)."""
from __future__ import annotations

from pathlib import Path
from typing import List

from core.models.tokens import Dissonance, RunArtifacts
from core.io.storage import save_json


def render_md(artifacts: RunArtifacts) -> str:
    lines: List[str] = []
    lines.append(f"# ProcessGhost Report -- Run {artifacts.run_id}")
    lines.append("")
    lines.append(f"Video: `{artifacts.video_path.name}`")
    lines.append("")
    lines.append(f"Total intents: {len(artifacts.intents)}  |  Total actions: {len(artifacts.actions)}  |  Dissonances: {len(artifacts.dissonances)}")
    lines.append("")
    for d in artifacts.dissonances:
        lines.append(f"## {d.kind} -- {d.title}")
        lines.append(f"- Severity: **{d.severity}**  |  t={d.t_s:.1f}s")
        lines.append(f"- Explanation: {d.explanation}")
        if d.evidence.frame_paths:
            frame_list = ", ".join(f"`{p.name}`" for p in d.evidence.frame_paths)
            lines.append(f"- Frames: {frame_list}")
        if d.evidence.transcript_excerpt:
            lines.append(f"- Transcript: \"{d.evidence.transcript_excerpt}\"")
        lines.append("")
    if not artifacts.dissonances:
        lines.append("_No dissonances detected in this run._")
    return "\n".join(lines)


def render_html(md_text: str) -> str:
    body = md_text.replace("\n", "<br>")
    return f"<html><body>{body}</body></html>"


def write_reports(artifacts: RunArtifacts, run_dir: Path) -> RunArtifacts:
    md_text = render_md(artifacts)
    md_path = run_dir / "report.md"
    html_path = run_dir / "report.html"
    md_path.write_text(md_text, encoding="utf-8")
    html_path.write_text(render_html(md_text), encoding="utf-8")
    artifacts.report_md = md_path
    artifacts.report_html = html_path
    # also persist dissonances JSON for audit
    save_json([d.model_dump() for d in artifacts.dissonances], run_dir / "dissonances.json")
    return artifacts
