"""Build the screen timeline for a video and write inspection artifacts. No API calls.

Writes to runs/screen-<timestamp>/ (or --out):
  screen_events.json   the ScreenTimeline (events, scenes, per-sample activity)
  keyframes/           full-resolution before/after frame of every event
  events_contact.png   one row per event: before | after with changed boxes in red
  activity.png         changed fraction over time with event spans (and transcript spans if given)

Usage (from the processghost/ directory):
    python -m scripts.screen_timeline ../data/english_hackathon_test.mp4
    python -m scripts.screen_timeline video.mp4 --transcript runs/<run>/run.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from core import config  # noqa: E402
from core.models.screen import ScreenTimeline  # noqa: E402
from core.screen.timeline import build_screen_timeline  # noqa: E402

THUMB_W = 520
_COLORS = {"local": "tab:blue", "large_shift": "tab:orange", "scene_cut": "tab:red"}


def _thumb(path: Path, boxes, width: int):
    img = cv2.imread(str(path))
    scale = THUMB_W / img.shape[1]
    thumb = cv2.resize(img, (THUMB_W, int(img.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    for x, y, w, h in boxes:
        cv2.rectangle(thumb, (int(x * scale), int(y * scale)), (int((x + w) * scale), int((y + h) * scale)), (0, 0, 255), 2)
    return thumb


def write_contact_sheet(tl: ScreenTimeline, out_dir: Path) -> Optional[Path]:
    rows = []
    for ev in tl.events:
        before = _thumb(out_dir / ev.before_frame, [], tl.width)
        after = _thumb(out_dir / ev.after_frame, ev.bboxes, tl.width)
        label = "%s %s t=%.1f-%.1fs changed=%.2f%% cover=%.0f%% boxes=%d" % (
            ev.event_id, ev.type, ev.t_start, ev.t_end, ev.changed_pct, ev.coverage_pct, len(ev.bboxes)
        )
        bar = 255 * __import__("numpy").ones((26, THUMB_W * 2, 3), dtype="uint8")
        cv2.putText(bar, label, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
        rows.append(cv2.vconcat([bar, cv2.hconcat([before, after])]))
    if not rows:
        return None
    path = out_dir / "events_contact.png"
    cv2.imwrite(str(path), cv2.vconcat(rows))
    return path


def write_activity_plot(tl: ScreenTimeline, out_dir: Path, transcript: List[dict]) -> Path:
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.plot([a[0] for a in tl.activity], [a[1] * 100 for a in tl.activity], lw=1, color="gray")
    for ev in tl.events:
        ax.axvspan(ev.t_start, ev.t_end + 0.2, color=_COLORS[ev.type], alpha=0.35)
        ax.text(ev.t_start, ax.get_ylim()[1] * 0.92, ev.event_id[3:], fontsize=7)
    for seg in transcript:
        ax.axvspan(seg["start_s"], seg["end_s"], ymin=0, ymax=0.04, color="green", alpha=0.6)
    ax.set_yscale("symlog", linthresh=0.01)
    ax.set_xlabel("seconds")
    ax.set_ylabel("% pixels changed vs previous sample (symlog)")
    ax.set_title("Screen activity (blue=local, orange=scroll, red=scene cut; green strip=speech)")
    path = out_dir / "activity.png"
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("video", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--fps", type=float, default=config.SCREEN_FPS)
    parser.add_argument("--transcript", type=Path, default=None, help="a run.json whose transcript to overlay")
    args = parser.parse_args()

    out_dir = args.out or (config.RUNS_DIR / ("screen-" + datetime.utcnow().strftime("%Y%m%d-%H%M%S")))
    out_dir.mkdir(parents=True, exist_ok=True)

    tl = build_screen_timeline(args.video, out_dir, fps=args.fps)
    (out_dir / "screen_events.json").write_text(tl.model_dump_json(indent=2), encoding="utf-8")

    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))["transcript"] if args.transcript else []
    write_contact_sheet(tl, out_dir)
    write_activity_plot(tl, out_dir, transcript)

    print("video %.1fs %dx%d, %d events, %d scenes" % (tl.duration_s, tl.width, tl.height, len(tl.events), len(tl.scenes)))
    for ev in tl.events:
        print(
            "%s %-11s %5.1f-%5.1fs changed=%6.2f%% cover=%5.1f%% boxes=%2d %s"
            % (ev.event_id, ev.type, ev.t_start, ev.t_end, ev.changed_pct, ev.coverage_pct, len(ev.bboxes),
               (ev.scene_label or "")[:70].encode("ascii", "replace").decode())
        )
    print("wrote %s" % out_dir)


if __name__ == "__main__":
    main()
