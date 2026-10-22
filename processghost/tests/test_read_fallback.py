"""Reading fallback: changed-input OCR retries, then one batched cheap-model read behind a validator.
Synthetic frames and fake OCR/client: no video, no real OCR, no API."""
from __future__ import annotations

import cv2
import numpy as np

from core import config
from core.models.screen import ChangeRecord, ScreenEvent, ScreenTimeline
from core.screen.read_fallback import VlmReadOutput, VlmReading, repair_records, valid_cell_ref
from core.screen.surfaces import diff_locators, locate
from tests.fakes import _FakeClient

GOOD = "Budget.xlsx | C7 | fx | 4500 | Sheet 1 of 3 | PageStyle_Budget"
GARBLED = "Budget.xlsx | Sheet 1 of 3 | PageStyle_Budget"
H, W = 100, 200


def _frame(path, value):
    cv2.imwrite(str(path), np.full((H, W, 3), value, dtype=np.uint8))


def _timeline(tmp_path, n=1):
    events = []
    for i in range(1, n + 1):
        _frame(tmp_path / ("b%d.png" % i), 250)
        _frame(tmp_path / ("a%d.png" % i), 240)
        events.append(
            ScreenEvent(
                event_id="ev-%03d" % i, t_start=float(i), t_end=i + 0.3, type="local", changed_pct=1.0,
                coverage_pct=1.0, before_frame="b%d.png" % i, after_frame="a%d.png" % i,
            )
        )
    return ScreenTimeline(video="v", duration_s=10.0, width=W, height=H, events=events)


def _records(n=1):
    loc = locate(GARBLED)
    return [
        ChangeRecord(
            event_id="ev-%03d" % i, t_start=float(i), t_end=i + 0.3, type="local",
            surface_before=loc, surface_after=loc, surface_change=diff_locators(loc, loc),
        )
        for i in range(1, n + 1)
    ]


def _ocr_good_only_when_upscaled(img) -> str:
    return GOOD if img.shape[1] > W else GARBLED  # strips keep the frame width, so width shows an upscale


def _ocr_never(_img) -> str:
    return GARBLED


def test_validator_accepts_cell_references_only():
    assert valid_cell_ref("F18") and valid_cell_ref(" ab12 ") and not valid_cell_ref("Fx") and not valid_cell_ref("")


def test_a_changed_input_retry_repairs_the_field_without_any_model_call(tmp_path):
    client = _FakeClient()
    out, stats = repair_records(_records(), _timeline(tmp_path), tmp_path, _ocr_good_only_when_upscaled, client, tmp_path / "w")
    assert out[0].surface_after.focus == "C7" and out[0].surface_after.read_by["focus"] == "ocr_retry"
    assert stats["ocr_retry_fixed"] == 2 and stats["vlm_queued"] == 0 and client.structured_calls == []


def test_unfixable_fields_go_to_one_batched_cheap_model_call(tmp_path):
    client = _FakeClient(structured_response=VlmReadOutput(readings=[
        VlmReading(id="ev-001/before", cell_ref="A1", content=""),
        VlmReading(id="ev-001/after", cell_ref="c7", content="4500"),
        VlmReading(id="ev-002/before", cell_ref="A1"),
        VlmReading(id="ev-002/after", cell_ref="D4", content="x"),
    ]))
    out, stats = repair_records(_records(2), _timeline(tmp_path, 2), tmp_path, _ocr_never, client, tmp_path / "w")
    assert len(client.structured_calls) == 1 and len(client.structured_calls[0][3]) == 4  # one call, four crops
    assert stats["vlm_fixed"] == 4
    after = out[0].surface_after
    assert after.focus == "C7" and after.focus_content == "4500" and after.read_by == {"focus": "vlm", "focus_content": "vlm"}
    assert out[0].surface_change == "same_surface_edit"  # recomputed from the repaired locators


def test_a_model_answer_that_fails_the_validator_is_rejected_and_the_field_stays_unread(tmp_path):
    client = _FakeClient(structured_response=VlmReadOutput(readings=[
        VlmReading(id="ev-001/before", cell_ref="not a cell"), VlmReading(id="ev-001/after", cell_ref=""),
    ]))
    out, stats = repair_records(_records(), _timeline(tmp_path), tmp_path, _ocr_never, client, tmp_path / "w")
    assert stats["vlm_rejected"] == 2 and stats["vlm_fixed"] == 0
    assert out[0].surface_after.focus == "" and out[0].surface_after.read_by == {}


def test_over_the_cap_the_extra_crops_are_not_sent_and_stay_unread(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VLM_READ_MAX_CROPS", 1)
    client = _FakeClient(structured_response=VlmReadOutput(readings=[VlmReading(id="ev-001/before", cell_ref="B2")]))
    out, stats = repair_records(_records(), _timeline(tmp_path), tmp_path, _ocr_never, client, tmp_path / "w")
    assert stats["vlm_queued"] == 1 and stats["over_cap"] == 1 and len(client.structured_calls[0][3]) == 1
    assert out[0].surface_before.focus == "B2" and out[0].surface_after.focus == ""


def test_readable_fields_cost_nothing(tmp_path):
    good = locate(GOOD)
    rec = ChangeRecord(event_id="ev-001", t_start=1.0, t_end=1.3, type="local", surface_before=good, surface_after=good)
    client = _FakeClient()
    out, stats = repair_records([rec], _timeline(tmp_path), tmp_path, _ocr_never, client, tmp_path / "w")
    assert stats["failed"] == 0 and client.structured_calls == [] and out[0] is rec
