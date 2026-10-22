"""Context map (Trigger A) and speech clue on synthetic events: no video, no OCR engine, no API."""
from __future__ import annotations

from pathlib import Path
from typing import List

import cv2
import numpy as np

from core.models.screen import ChangeRecord, ScreenEvent, ScreenTimeline
from core.screen.context import build_contexts, context_key
from core.screen.speech_clue import attach_speech_clues, locator_tokens
from core.screen.surfaces import diff_locators, locate

BUDGET = "Budget.xlsx - LibreOffice Calc | C7 | fx | 4500 | Sheet 1 of 3 | PageStyle_Budget"
BUDGET_EDIT = "Budget.xlsx - LibreOffice Calc | D9 | fx | 12 | Sheet 1 of 3 | PageStyle_Budget"
ACTUALS = "Budget.xlsx - LibreOffice Calc | A1 | fx | Vendor | Sheet 2 of 3 | PageStyle_MonthlyActuals"


def _png(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((20, 40, 3), 200, dtype=np.uint8))
    return path.name


def _event(n: int, t: float, etype: str, tmp: Path) -> ScreenEvent:
    _png(tmp / "k" / f"b{n}.png")
    _png(tmp / "k" / f"a{n}.png")
    return ScreenEvent(
        event_id=f"ev-{n:03d}", t_start=t, t_end=t + 0.3, type=etype, changed_pct=1.0, coverage_pct=1.0,
        before_frame=f"k/b{n}.png", after_frame=f"k/a{n}.png",
    )


def _record(n: int, t: float, before: str, after: str) -> ChangeRecord:
    lb, la = locate(before), locate(after)
    return ChangeRecord(
        event_id=f"ev-{n:03d}", t_start=t, t_end=t + 0.3, type="local", chrome_before=before, chrome_after=after,
        surface_before=lb, surface_after=la, surface_change=diff_locators(lb, la),
    )


def _build(tmp_path: Path, steps: List[tuple]):
    events = [_event(i + 1, t, ty, tmp_path) for i, (t, ty, _b, _a) in enumerate(steps)]
    records = [_record(i + 1, t, b, a) for i, (t, _ty, b, a) in enumerate(steps)]
    tl = ScreenTimeline(video="v", duration_s=30.0, width=40, height=20, events=events)
    return build_contexts(tl, records, tmp_path, lambda img: "LABELS Vendor Amount")


def test_sheet_switch_creates_a_context_and_returning_reuses_it(tmp_path):
    contexts, tl, records = _build(
        tmp_path,
        [
            (2.0, "local", BUDGET, BUDGET_EDIT),
            (5.0, "scene_cut", BUDGET_EDIT, ACTUALS),
            (9.0, "scene_cut", ACTUALS, BUDGET),
        ],
    )
    assert [c.context_id for c in contexts] == ["ctx-001", "ctx-002"]
    assert [e.context_id for e in tl.events] == ["ctx-001", "ctx-002", "ctx-001"]
    assert [r.context_id for r in records] == ["ctx-001", "ctx-002", "ctx-001"]
    first = contexts[0]
    assert first.seen_count == 2  # entered at the start and again on return
    assert first.event_ids == ["ev-001", "ev-003"]


def test_same_surface_edits_stay_in_one_context(tmp_path):
    contexts, tl, _ = _build(tmp_path, [(2.0, "local", BUDGET, BUDGET_EDIT), (6.0, "local", BUDGET_EDIT, BUDGET)])
    assert len(contexts) == 1 and {e.context_id for e in tl.events} == {"ctx-001"}


def test_context_holds_observations_only(tmp_path):
    contexts, _, _ = _build(tmp_path, [(5.0, "scene_cut", BUDGET, ACTUALS)])
    ctx = next(c for c in contexts if c.locator.location == "MonthlyActuals")
    assert ctx.labels == "LABELS Vendor Amount" and ctx.locator.index == 2
    assert not hasattr(ctx, "hypotheses")


def test_unrecognised_scene_cut_gets_its_own_context(tmp_path):
    contexts, tl, _ = _build(tmp_path, [(5.0, "scene_cut", "Tool One | menu", "Tool Two | menu")])
    assert len(contexts) == 2 and tl.events[0].context_id == "ctx-002"


def test_context_key_is_stable_under_sheet_name_noise():
    assert context_key(locate(BUDGET)) == context_key(locate(BUDGET.replace("PageStyle_Budget", "PageStyle_Budqet")))


# --- speech clue --------------------------------------------------------------------


def _segs(*words):
    return [{"start": 0.0, "end": 30.0, "text": "", "words": [{"word": " " + w, "start": t, "end": t + 0.3} for w, t in words]}]


def test_locator_tokens_split_camel_case_and_drop_noise():
    assert locator_tokens(locate(ACTUALS)) == ["monthly", "actuals"]
    assert locator_tokens(locate("Budget.xlsx | B2 | fx | x | Sheet 2 of 3")) == []  # 'Sheet 2' names nothing


def test_speech_naming_the_destination_after_the_action_is_a_clue_with_lag(tmp_path):
    rec = _record(1, 5.0, BUDGET, ACTUALS)
    out = attach_speech_clues([rec], _segs(("now", 8.6), ("monthly", 9.0), ("actuals", 9.4)))[0]
    assert out.speech_clue.found and out.speech_clue.matched == ["monthly", "actuals"]
    assert out.speech_clue.lag_s == 4.0


def test_a_switch_nobody_named_has_no_clue_but_is_not_an_error():
    rec = _record(1, 5.0, BUDGET, ACTUALS)
    out = attach_speech_clues([rec], _segs(("click", 4.0), ("here", 4.5)))[0]
    assert out.speech_clue.found is False and out.speech_clue.nav_words == ["click"]


def test_speech_outside_the_window_does_not_count():
    rec = _record(1, 5.0, BUDGET, ACTUALS)
    out = attach_speech_clues([rec], _segs(("actuals", 20.0)))[0]
    assert out.speech_clue.found is False


def test_edits_on_the_same_surface_have_no_clue_to_look_for():
    rec = _record(1, 5.0, BUDGET, BUDGET_EDIT)
    assert attach_speech_clues([rec], _segs(("actuals", 5.0)))[0].speech_clue is None
