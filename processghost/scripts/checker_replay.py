"""Replay what a past run's Checker was shown and what it said -- no API calls.

For every flag the Checker touched in a finished run, rebuilds: the question and excerpt it was
given, the frames it was sent (with the pixel-diff boxes between consecutive frames drawn on them),
and its answer from trace.jsonl. Writes <run_dir>/checker_replay.html (open it in a browser),
checker_replay.json and checker_replay_assets/. The point is to see, per flag, whether the screen
evidence actually carried the answer or the model just echoed the step/transcript text it was also
given (the "echo score": share of the observation's content words already present in the prompt).

Usage (from the processghost/ directory):
    python -m scripts.checker_replay runs/20260830-160732-e1b16b [more run dirs ...]
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional

import cv2

from core import config
from core.io.video import frame_name_for, subsample_evenly
from core.models.sop import Flag, SOPDocument
from core.models.tokens import TranscriptSegment
from core.pipeline import _flag_step_excerpt, _flag_transcript_excerpt, _flag_window_s
from core.screen.diff import diff_boxes
from core.text_grounding import normalize_for_quote_match

THUMB_WIDTH = 360
_SEVERITY_ORDER = {"blocking": 0, "caution": 1, "note": 2}
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "this", "that", "these", "those", "with", "from", "into", "then", "than", "when", "what", "where",
    "which", "while", "will", "would", "could", "should", "there", "here", "have", "been", "were",
    "does", "each", "some", "such", "step", "shows", "visible", "screen", "sheet",
}


def _content_words(text: str) -> set:
    return {w for w in _WORD_RE.findall(normalize_for_quote_match(text)) if len(w) >= 4 and w not in _STOP}


def _load_trace_answers(trace_path: Path, checked: List[Flag]) -> Dict[str, dict]:
    """Map flag_id -> {'payload': {...}} or {'error': str}. Older traces carry no flag_id on the
    payload line, so payloads are matched by call order against `checked` (flags the Checker
    attempted, in the order it attempted them), after removing flags that logged a failure."""
    answers: Dict[str, dict] = {}
    failed_ids = set()
    payloads: List[dict] = []
    if not trace_path.exists():
        return answers
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        if ev.get("stage") != "checker":
            continue
        if ev.get("event") == "failure" and (ev.get("meta") or {}).get("flag_id"):
            fid = ev["meta"]["flag_id"]
            failed_ids.add(fid)
            answers[fid] = {"error": ev.get("error", "")}
        elif ev.get("event") == "payload":
            payloads.append(ev["payload"])
    remaining = [f.flag_id for f in checked if f.flag_id not in failed_ids]
    unmatched = []
    for p in payloads:
        if p.get("flag_id") in {f.flag_id for f in checked}:
            answers[p["flag_id"]] = {"payload": p}
        else:
            unmatched.append(p)
    free = [fid for fid in remaining if fid not in answers]
    for fid, p in zip(free, unmatched):
        answers[fid] = {"payload": p}
    return answers


def _frames_sent(flag: Flag, doc: SOPDocument, transcript: List[TranscriptSegment], frames_dir: Path) -> List[Path]:
    """Recompute the frames extract_frames_window picked for this flag (same grid, then the same
    even subsample), keeping only ones still on disk."""
    start_s, end_s = _flag_window_s(flag, doc, transcript)
    step = config.CHECKER_FRAME_INTERVAL_S
    start = max(0.0, math.floor(max(0.0, start_s) / step) * step)
    end = math.ceil(end_s / step) * step
    n = int(round((end - start) / step))
    paths = [frames_dir / frame_name_for(start + i * step) for i in range(max(n, 0))]
    paths = [p for p in paths if p.exists()]
    return subsample_evenly(paths, config.CHECKER_MAX_FRAMES_PER_FLAG)


def _annotate(img, boxes, scale):
    thumb = cv2.resize(img, (THUMB_WIDTH, int(img.shape[0] * THUMB_WIDTH / img.shape[1])), interpolation=cv2.INTER_AREA)
    for x, y, w, h in boxes:
        cv2.rectangle(thumb, (int(x * scale), int(y * scale)), (int((x + w) * scale), int((y + h) * scale)), (0, 0, 255), 2)
    return thumb


def _replay_run(run_dir: Path) -> dict:
    doc = SOPDocument.model_validate(json.loads((run_dir / "sop.json").read_text(encoding="utf-8")))
    run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    transcript = [TranscriptSegment(**seg) for seg in run_json["transcript"]]
    frames_dir = run_dir / "checker_frames"
    assets = run_dir / "checker_replay_assets"
    assets.mkdir(exist_ok=True)

    checked = sorted((f for f in doc.flags if f.status != "unchecked"), key=lambda f: _SEVERITY_ORDER.get(f.severity, 3))
    answers = _load_trace_answers(run_dir / "trace.jsonl", checked)
    resolutions = {r.flag_id: r for r in doc.resolutions}

    cards = []
    for flag in checked:
        ev = resolutions[flag.flag_id].evidence if flag.flag_id in resolutions else []
        paths = [frames_dir / Path(str(e.frame_path)).name for e in ev] or _frames_sent(flag, doc, transcript, frames_dir)
        paths = [p for p in paths if p.exists()]

        frames_out = []
        prev = None
        for i, p in enumerate(paths):
            img = cv2.imread(str(p))
            if img is None:
                continue
            res = diff_boxes(prev, img) if prev is not None else None
            scale = THUMB_WIDTH / img.shape[1]
            asset = assets / f"{flag.flag_id}_{i:02d}.jpg"
            cv2.imwrite(str(asset), _annotate(img, res.boxes if res else [], scale), [cv2.IMWRITE_JPEG_QUALITY, 80])
            frames_out.append(
                {
                    "name": p.name,
                    "asset": f"checker_replay_assets/{asset.name}",
                    "n_boxes": len(res.boxes) if res else None,
                    "changed_frac": round(res.changed_frac, 4) if res else None,
                    "identical": res.identical if res else None,
                }
            )
            prev = img

        context = " ".join(
            [flag.question]
            + [f"{s['title']} {s['instruction']}" for s in _flag_step_excerpt(doc, flag)]
            + [s["text"] for s in _flag_transcript_excerpt(flag, transcript)]
        )
        ans = answers.get(flag.flag_id, {})
        payload = ans.get("payload") or {}
        observed = payload.get("observed", "")
        obs_words = _content_words(observed)
        echo = (len(obs_words & _content_words(context)) / len(obs_words)) if obs_words else None
        diffs = [f for f in frames_out if f["identical"] is not None]
        cards.append(
            {
                "flag_id": flag.flag_id,
                "family": flag.family,
                "severity": flag.severity,
                "status": flag.status,
                "question": flag.question,
                "context": context,
                "frames": frames_out,
                "redundant_frames": sum(1 for f in diffs if f["identical"]),
                "diffed_pairs": len(diffs),
                "observed": observed,
                "verdict": payload.get("verdict"),
                "inferred_text": payload.get("inferred_text"),
                "reason": payload.get("reason"),
                "error": ans.get("error"),
                "echo_score": None if echo is None else round(echo, 2),
                "new_words": sorted(obs_words - _content_words(context)),
            }
        )
    result = {"run_id": run_dir.name, "flags": cards}
    (run_dir / "checker_replay.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (run_dir / "checker_replay.html").write_text(_render_html(result), encoding="utf-8")
    return result


def _render_html(result: dict) -> str:
    esc = html.escape
    parts = [
        "<!doctype html><meta charset='utf-8'><title>Checker replay</title>",
        "<style>body{font-family:sans-serif;max-width:1500px;margin:16px auto;padding:0 16px}"
        ".card{border:1px solid #bbb;border-radius:6px;padding:12px;margin:16px 0}"
        ".strip{display:flex;flex-wrap:wrap;gap:6px}.fr{font-size:11px;width:360px}"
        ".fr img{width:360px;display:block}.same{outline:3px solid #d90}"
        ".echo-high{color:#b00;font-weight:bold}pre{white-space:pre-wrap;background:#f5f5f5;padding:8px}</style>",
        f"<h1>Checker replay: {esc(result['run_id'])}</h1>",
        "<p>Red boxes = pixels changed since the previous frame. Orange outline = identical to the previous frame (redundant).</p>",
    ]
    for c in result["flags"]:
        echo = c["echo_score"]
        echo_cls = "echo-high" if echo is not None and echo >= 0.7 else ""
        parts.append(f"<div class='card'><h2>{esc(c['flag_id'])} [{esc(c['family'])}, {esc(c['severity'])}] status={esc(c['status'])}</h2>")
        parts.append(f"<p><b>Question:</b> {esc(c['question'])}</p><details><summary>Prompt context</summary><pre>{esc(c['context'])}</pre></details>")
        parts.append(f"<p>Frames sent: {len(c['frames'])}; identical to previous: {c['redundant_frames']} of {c['diffed_pairs']} pairs</p><div class='strip'>")
        for f in c["frames"]:
            cls = "fr same" if f["identical"] else "fr"
            cap = f"{esc(f['name'])}" + ("" if f["n_boxes"] is None else f" | boxes={f['n_boxes']} changed={f['changed_frac']}")
            parts.append(f"<div class='{cls}'><img src='{esc(f['asset'])}'>{cap}</div>")
        parts.append("</div>")
        if c["error"]:
            parts.append(f"<p><b>Call failed:</b> {esc(c['error'][:300])}</p>")
        else:
            parts.append(f"<p><b>Verdict:</b> {esc(str(c['verdict']))} | <span class='{echo_cls}'>echo score: {echo}</span> "
                         f"| words not in the prompt: {esc(', '.join(c['new_words']) or '(none)')}</p>")
            parts.append(f"<p><b>Observed:</b></p><pre>{esc(c['observed'])}</pre>")
            if c["inferred_text"] or c["reason"]:
                parts.append(f"<p><b>Answer:</b> {esc(c['inferred_text'] or c['reason'] or '')}</p>")
        parts.append("</div>")
    return "\n".join(parts)


def _summarize(results: List[dict]) -> None:
    print("=" * 70)
    print("%-26s %5s %9s %11s %9s" % ("run", "flags", "failed", "redundant", "mean echo"))
    all_cards: List[dict] = []
    for r in results:
        cards = r["flags"]
        all_cards.extend(cards)
        pairs = sum(c["diffed_pairs"] for c in cards)
        same = sum(c["redundant_frames"] for c in cards)
        echoes = [c["echo_score"] for c in cards if c["echo_score"] is not None]
        print(
            "%-26s %5d %9d %10s %9s"
            % (
                r["run_id"],
                len(cards),
                sum(1 for c in cards if c["error"]),
                "%d/%d" % (same, pairs),
                "%.2f" % (sum(echoes) / len(echoes)) if echoes else "n/a",
            )
        )
    print("-" * 70)
    for verdict in ("resolved", "contradicted", "unresolvable"):
        e = [c["echo_score"] for c in all_cards if c["verdict"] == verdict and c["echo_score"] is not None]
        print("echo score, verdict=%-13s n=%2d mean=%s" % (verdict, len(e), "%.2f" % (sum(e) / len(e)) if e else "n/a"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    results = []
    for run_dir in args.run_dirs:
        results.append(_replay_run(run_dir))
        print("wrote %s" % (run_dir / "checker_replay.html"))
    _summarize(results)


if __name__ == "__main__":
    main()
