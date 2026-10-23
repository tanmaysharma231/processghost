"""Dry run of the adaptive evidence layer: which stretches of a recording would be investigated, and why.
Makes NO model calls (local pixel diffs and local OCR only), so it costs nothing and shows the candidates
before any vision spend.

Writes to runs/dryrun-<timestamp>/ (or --out):
  screen_events.json, change_records.json, contexts.json   the index
  investigations.json                                      merged triggers with reasons and evidence ids
  investigations_contact.png                               one row per investigation: before | peak/after | after

With --run runs/<id> the transcript-side Trigger B and the explained/unexplained split come from that
run's sop.json; without it every screen event is unexplained by definition and only Triggers A and C fire.
Word timestamps are not stored in a run, so speech clues are not computed here.

Usage (from the processghost/ directory):
    python -m scripts.evidence_dry_run ../data/english_hackathon_test.mp4
    python -m scripts.evidence_dry_run video.mp4 --run runs/<run_id> --split-locus
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np

from core import config
from core.io import storage
from core.models.screen import Investigation, ScreenEvent, ScreenTimeline
from core.models.sop import SOPDocument
from core.models.tokens import TranscriptSegment
from core.screen.anchors import build_anchors, describe
from core.screen.classify import classify_records, unused_from_classes
from core.screen.context import build_contexts
from core.screen.investigate import attach_evidence_ids, build_investigations, explained_event_ids
from core.screen.ocr import get_ocr
from core.screen.read_fallback import repair_records
from core.screen.records import build_change_records, cached_ocr
from core.screen.timeline import build_screen_timeline

THUMB_W = 360


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode()


def _thumb(path: Path, boxes, width: int = THUMB_W) -> np.ndarray:
    img = cv2.imread(str(path))
    scale = width / img.shape[1]
    thumb = cv2.resize(img, (width, int(img.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    for x, y, w, h in boxes:
        cv2.rectangle(thumb, (int(x * scale), int(y * scale)), (int((x + w) * scale), int((y + h) * scale)), (0, 0, 255), 2)
    return thumb


def _bar(width: int, text: str) -> np.ndarray:
    bar = np.full((26, width, 3), 255, dtype=np.uint8)
    cv2.putText(bar, _ascii(text)[: width // 7], (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    return bar


def write_contact_sheet(invs: List[Investigation], timeline: ScreenTimeline, out_dir: Path) -> Optional[Path]:
    events: Dict[str, ScreenEvent] = {e.event_id: e for e in timeline.events}
    rows = []
    for inv in invs:
        reasons = ",".join(sorted({t.kind[0] for t in inv.triggers}))
        label = f"{inv.inv_id} t={inv.t_start:.1f}-{inv.t_end:.1f}s ctx={inv.context_id} triggers={reasons} events={','.join(inv.event_ids) or '-'}"
        evs = [events[e] for e in inv.event_ids if e in events]
        if not evs:
            rows.append(_bar(THUMB_W * 3, label + " (no screen change located)"))
            continue
        first, last = evs[0], evs[-1]
        mid = out_dir / first.peak_frame if first.peak_frame else out_dir / last.before_frame
        boxes = [b for e in evs for b in e.region_boxes()]
        thumbs = [_thumb(out_dir / first.before_frame, []), _thumb(mid, []), _thumb(out_dir / last.after_frame, boxes)]
        rows.append(cv2.vconcat([_bar(THUMB_W * 3, label), cv2.hconcat(thumbs)]))
    if not rows:
        return None
    path = out_dir / "investigations_contact.png"
    cv2.imwrite(str(path), cv2.vconcat(rows))
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("video", type=Path)
    parser.add_argument("--run", type=Path, default=None, help="a run dir whose sop.json supplies flags and steps")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--split-locus", action="store_true", help="enable SCREEN_SPLIT_ON_LOCUS_SHIFT for this run")
    args = parser.parse_args()

    ocr = get_ocr()
    if ocr is None:
        raise SystemExit("no local OCR engine available (pip install rapidocr-onnxruntime)")
    if args.split_locus:
        config.SCREEN_SPLIT_ON_LOCUS_SHIFT = True

    out_dir = args.out or storage.new_named_dir("dryrun")
    out_dir.mkdir(parents=True, exist_ok=True)

    timeline = build_screen_timeline(args.video, out_dir, ocr=ocr)
    records = build_change_records(timeline, out_dir, ocr)
    records, read_stats = repair_records(records, timeline, out_dir, cached_ocr(ocr), None, out_dir / "reads")
    records = classify_records(records)
    anchors, unused_selections = build_anchors(records, [])  # word timestamps are not stored in a run: value signal only
    contexts, timeline, records = build_contexts(timeline, records, out_dir, cached_ocr(ocr))

    if args.run:
        doc = SOPDocument.model_validate_json((args.run / "sop.json").read_text(encoding="utf-8"))
        raw = json.loads((args.run / "run.json").read_text(encoding="utf-8")).get("transcript", [])
        transcript = [TranscriptSegment.model_validate(s) for s in raw]
    else:
        doc, transcript = SOPDocument(run_id="dry-run"), []
    invs = attach_evidence_ids(
        build_investigations(timeline, records, doc, transcript, explained_event_ids(doc)), timeline
    )

    (out_dir / "screen_events.json").write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
    storage.save_json([r.model_dump(mode="json") for r in records], out_dir / "change_records.json")
    storage.save_json([c.model_dump(mode="json") for c in contexts], out_dir / "contexts.json")
    storage.save_json([a.model_dump(mode="json") for a in anchors], out_dir / "anchors.json")
    storage.save_json([v.model_dump(mode="json") for v in invs], out_dir / "investigations.json")
    write_contact_sheet(invs, timeline, out_dir)

    print("video %.1fs %dx%d | model calls: 0 | events: %d | contexts: %d | investigations: %d"
          % (timeline.duration_s, timeline.width, timeline.height, len(timeline.events), len(contexts), len(invs)))
    if not args.run:
        print("(no --run: every event counts as unexplained, so only triggers A and C can fire)")
    print("")
    print("contexts:")
    for c in contexts:
        loc = c.locator
        print("  %s %-11s doc=%s location=%s seen=%dx events=%s"
              % (c.context_id, loc.surface, _ascii(loc.document) or "-", _ascii(loc.location) or "-", c.seen_count, ",".join(c.event_ids)))
    print("")
    print("event classes (fields repaired by re-reading: %d, queued for the cheap model but NOT sent in a dry run: %d):"
          % (read_stats["ocr_retry_fixed"], read_stats["vlm_queued"]))
    for r in records:
        print("  %s %5.1fs %-12s %s" % (r.event_id, r.t_start, r.event_class, _ascii(r.class_reason)))
    for a in anchors:
        print("  anchor %s %5.1fs %s role=%s signals=%s" % (a.event_id, a.t_s, _ascii(describe(a)), a.role, ",".join(a.signals) or "-"))
    for u in unused_from_classes(records) + unused_selections:
        print("  unused %s %5.1fs %s: %s" % (u.event_id, u.t_s, u.event_class, _ascii(u.reason)))
    print("")
    print("%-8s %-13s %-8s %-18s %s" % ("inv", "time", "context", "triggers", "events / reasons"))
    for v in invs:
        kinds = ",".join(sorted({t.kind[0] for t in v.triggers}))
        print("%-8s %5.1f-%5.1fs  %-8s %-18s %s" % (v.inv_id, v.t_start, v.t_end, v.context_id or "-", kinds, ",".join(v.event_ids) or "-"))
        for t in v.triggers:
            print("           %s %s: %s" % (t.kind[0], t.source_id, _ascii(t.detail)[:90]))
    print("\nwrote %s" % out_dir)


if __name__ == "__main__":
    main()
