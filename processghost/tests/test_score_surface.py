"""The surface scorer on fake data, plus a gated check of the surface locators on the one real clip.
The real-clip test needs the recording and the local OCR engine and is skipped without them; its labels
are DRAFTS (evals/cases/bills_tracker/expected_surface.json), so it is a regression check on what was
measured, not proof the locators are correct."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.screen.context import build_contexts
from core.screen.ocr import get_ocr
from core.screen.records import build_change_records, cached_ocr
from core.screen.timeline import build_screen_timeline
from evals.score_surface import load_labels, score_surface

LABELS = [
    {"label_id": "L1", "t_start": 1.0, "t_end": 2.0, "expect": {"change": "sheet_switch", "index": 2, "focus": "F18"}},
    {"label_id": "L2", "t_start": 5.0, "t_end": 6.0, "expect": {"index": 1}},
]


def _rec(event_id, t, change, index, focus="", location=""):
    return {
        "event_id": event_id, "t_start": t, "t_end": t + 0.3, "surface_change": change,
        "surface_after": {"index": index, "focus": focus, "location": location},
    }


def test_scores_only_the_listed_fields_per_field():
    result = score_surface([_rec("ev-001", 1.1, "sheet_switch", 2, "F18"), _rec("ev-002", 5.1, "same_surface_edit", 1, "Z9")], LABELS)
    assert result["matched"] == 2 and result["mismatches"] == []
    assert result["per_field"] == {"change": {"hits": 1, "scored": 1}, "index": {"hits": 2, "scored": 2}, "focus": {"hits": 1, "scored": 1}}


def test_a_wrong_field_is_reported_with_expected_and_actual():
    result = score_surface([_rec("ev-001", 1.1, "same_surface_edit", 2, "C18"), _rec("ev-002", 5.1, None, 1)], LABELS)
    bad = {(m["label"], m["field"]): (m["expected"], m["actual"]) for m in result["mismatches"]}
    assert bad == {("L1", "change"): ("sheet_switch", "same_surface_edit"), ("L1", "focus"): ("F18", "C18")}


def test_unmatched_labels_are_listed():
    assert score_surface([_rec("ev-001", 1.1, "sheet_switch", 2, "F18")], LABELS)["unmatched_labels"] == ["L2"]


def test_location_compares_without_spacing_or_case():
    label = [{"label_id": "L", "t_start": 0.0, "t_end": 1.0, "expect": {"location": "MonthlyActuals"}}]
    assert score_surface([_rec("ev-001", 0.1, None, 1, location="monthly actuals")], label)["mismatches"] == []


VIDEO = Path(__file__).resolve().parents[2] / "data" / "english_hackathon_test.mp4"


@pytest.mark.skipif(not VIDEO.exists() or get_ocr() is None, reason="needs the real clip and the local OCR engine")
def test_real_clip_locators_match_the_draft_labels(tmp_path):
    ocr = get_ocr()
    timeline = build_screen_timeline(VIDEO, tmp_path, ocr=ocr)
    records = build_change_records(timeline, tmp_path, ocr)
    contexts, _tl, records = build_contexts(timeline, records, tmp_path, cached_ocr(ocr))
    result = score_surface([r.model_dump(mode="json") for r in records], load_labels("bills_tracker"))
    # Measured 2026-10-18: every scored field matched. A drop here means an OCR or detector change.
    assert result["unmatched_labels"] == []
    assert result["mismatches"] == [], result["mismatches"]
    assert len(contexts) == 3 and max(c.seen_count for c in contexts) == 2  # one sheet is returned to and reused
