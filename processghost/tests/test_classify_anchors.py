"""Event classes by state change and selection anchors joined by time. Synthetic records: no video, no OCR, no API."""
from __future__ import annotations

from typing import List, Optional

from core.models.screen import ChangeRecord, Locator, RegionChange
from core.screen.anchors import build_anchors, describe
from core.screen.classify import (
    classify_record, classify_records, norm_text, sent_to_reconciler, text_changed, unused_from_classes,
)
from evals.score_classes import score_classes


def _loc(focus="", content="", location="Sheet A") -> Locator:
    return Locator(surface="spreadsheet", location=location, focus=focus, focus_content=content, confidence=0.9)


def _rec(n, t, *, type="local", regions=(), before=None, after=None, change="same_surface_edit", transient=False) -> ChangeRecord:
    return ChangeRecord(
        event_id="ev-%03d" % n, t_start=t, t_end=t + 0.3, type=type,
        regions=[RegionChange(box=(0, 0, 10, 10), before=b, after=a) for b, a in regions],
        surface_before=before or _loc(), surface_after=after or _loc(), surface_change=change, transient=transient,
    )


def _cls(rec: ChangeRecord) -> str:
    return classify_record(rec)[0]


def _words(*pairs) -> List[dict]:
    return [{"words": [{"word": w, "start": t} for w, t in pairs]}]


# --- text comparison ------------------------------------------------------------------


def test_thousands_separators_and_spacing_are_not_a_change():
    assert norm_text("45,000") == norm_text("45000")
    assert not text_changed("AMZN WEB SVCS", "AMZNWEBSVCS")
    assert not text_changed("Total Actualized 45000", "Total Actualized 45,000")


def test_real_changes_are_changes_and_ocr_jitter_is_not():
    assert text_changed("", "45000") and text_changed("45000", "")
    assert text_changed("Status Ravi K", "Status Paid")
    assert not text_changed("Variance Report March 2025", "Varlance Report March 2025")  # one glyph of jitter
    assert not text_changed("", "")


# --- classes --------------------------------------------------------------------------


def test_a_dropdown_that_opened_and_closed_is_transient_however_big():
    rec = _rec(1, 2.0, regions=[("", "Option A Option B Option C")], transient=True)
    assert _cls(rec) == "transient"


def test_a_scroll_with_nothing_left_is_view_only_but_a_scroll_with_an_edit_is_not():
    assert _cls(_rec(1, 2.0, type="large_shift")) == "view_only"
    assert _cls(_rec(2, 4.0, type="large_shift", regions=[("0", "45000")])) == "data_change"


def test_a_one_cell_paste_is_a_data_change():
    assert _cls(_rec(1, 2.0, regions=[("", "45000")])) == "data_change"


def test_a_total_recalculating_is_a_data_change():
    assert _cls(_rec(1, 2.0, regions=[("Total 0", "Total 45000")])) == "data_change"


def test_a_formula_edit_on_the_same_cell_is_a_data_change_via_the_formula_bar():
    rec = _rec(1, 2.0, before=_loc("F27", "=SUM(F1:F5)"), after=_loc("F27", "=SUMIFS(F1:F5,H1:H5,x)"))
    assert _cls(rec) == "data_change"


def test_moving_the_selection_changes_the_formula_bar_but_is_a_selection_not_a_data_change():
    rec = _rec(1, 2.0, before=_loc("C2", "45000"), after=_loc("F18", "12000"))
    cls, reason = classify_record(rec)
    assert cls == "selection" and "C2" in reason and "F18" in reason


def test_a_sheet_switch_is_navigation_even_when_all_the_text_changed():
    rec = _rec(1, 2.0, type="scene_cut", regions=[("a b c", "x y z")], change="sheet_switch")
    assert _cls(rec) == "navigation"


def test_nothing_readable_is_unclear_and_unclear_is_kept():
    rec = _rec(1, 2.0)
    assert _cls(rec) == "unclear"
    assert sent_to_reconciler(classify_records([rec])) != []


def test_only_proven_valueless_events_are_dropped_and_listed_with_a_reason():
    recs = classify_records([
        _rec(1, 1.0, transient=True),
        _rec(2, 3.0, type="large_shift"),
        _rec(3, 5.0, regions=[("", "45000")]),
        _rec(4, 7.0),
    ])
    assert [r.event_id for r in sent_to_reconciler(recs)] == ["ev-003", "ev-004"]
    unused = unused_from_classes(recs)
    assert [(u.event_id, u.event_class) for u in unused] == [("ev-001", "transient"), ("ev-002", "view_only")]
    assert all(u.reason for u in unused)


# --- anchors --------------------------------------------------------------------------


def _scenario(*, speech, extra_holder=False, second_paste=False):
    """A selection of C2 (content 45000) at t=10, then a paste of 45000 at t=18."""
    recs = [
        _rec(1, 10.0, before=_loc("A1", ""), after=_loc("C2", "45000")),
        _rec(2, 18.0, regions=[("", "45000")], after=_loc("F18", "45000")),
    ]
    if second_paste:
        recs.append(_rec(3, 22.0, regions=[("", "45000")], after=_loc("F19", "45000")))
    if extra_holder:
        recs.append(_rec(4, 12.0, before=_loc("A1", ""), after=_loc("D2", "45000")))
    recs = classify_records(recs)
    return build_anchors(recs, speech)


def test_pointer_word_plus_value_that_reappears_confirms_a_source_anchor():
    anchors, unused = _scenario(speech=_words(("copy", 10.5), ("this", 11.0)))
    a = anchors[0]
    assert a.confirmed and a.role == "source" and a.focus == "C2" and a.target_event_id == "ev-002"
    assert unused == []
    assert describe(a).startswith("C2 (source")


def test_the_cells_value_spoken_counts_as_the_speech_signal():
    anchors, _ = _scenario(speech=_words(("45000", 12.0)))
    assert anchors[0].confirmed


def test_speech_alone_is_only_a_candidate_worded_as_possibly():
    recs = classify_records([_rec(1, 10.0, before=_loc("A1", ""), after=_loc("C2", "45000"))])
    anchors, unused = build_anchors(recs, _words(("this", 11.0)))
    assert not anchors[0].confirmed and anchors[0].role == "pointer"
    assert describe(anchors[0]) == "possibly C2" and unused == []


def test_value_alone_is_only_a_candidate():
    anchors, _ = _scenario(speech=_words(("hello", 1.0)))
    assert not anchors[0].confirmed and anchors[0].role == "source" and anchors[0].target_event_id == "ev-002"


def test_a_selection_with_no_link_is_listed_as_unused_screen_activity():
    recs = classify_records([_rec(1, 10.0, before=_loc("A1", ""), after=_loc("C2", "stuff"))])
    anchors, unused = build_anchors(recs, _words(("hello", 1.0)))
    assert anchors[0].role is None
    assert [(u.event_id, u.event_class) for u in unused] == [("ev-001", "selection")]


def test_the_same_value_in_two_later_changes_is_never_guessed():
    anchors, _ = _scenario(speech=_words(("this", 11.0)), second_paste=True)
    a = anchors[0]
    assert not a.confirmed and a.target_event_id is None and "value_ambiguous" in a.signals


def test_two_anchors_holding_the_same_value_withhold_the_value_signal():
    anchors, _ = _scenario(speech=_words(("this", 11.0)), extra_holder=True)
    assert all(not a.confirmed and a.target_event_id is None for a in anchors if a.content == "45000")


def test_speech_outside_the_window_does_not_count():
    anchors, _ = _scenario(speech=_words(("this", 40.0)))
    assert not any(s.startswith("speech") for s in anchors[0].signals)


def test_a_navigation_that_landed_on_a_readable_cell_can_be_an_anchor_but_is_never_listed_as_unused():
    recs = classify_records([_rec(1, 10.0, type="scene_cut", change="sheet_switch", after=_loc("C2", "45000", "Sheet B"))])
    anchors, unused = build_anchors(recs, [])
    assert anchors[0].focus == "C2" and anchors[0].role is None and unused == []


# --- class scorer ---------------------------------------------------------------------


def test_class_scorer_counts_correct_and_flags_any_data_change_that_would_be_dropped():
    labels = [
        {"label_id": "L1", "t_start": 1.0, "t_end": 1.3, "class": "selection"},
        {"label_id": "L2", "t_start": 5.0, "t_end": 5.3, "class": "data_change"},
    ]
    good = [
        {"event_id": "e1", "t_start": 1.0, "t_end": 1.3, "event_class": "selection"},
        {"event_id": "e2", "t_start": 5.0, "t_end": 5.3, "event_class": "data_change"},
    ]
    assert score_classes(good, labels)["correct"] == 2 and score_classes(good, labels)["data_changes_dropped"] == []
    bad = [dict(good[0]), dict(good[1], event_class="view_only")]
    r = score_classes(bad, labels)
    assert r["correct"] == 1 and r["data_changes_dropped"] == ["L2"] and r["wrong"][0]["actual"] == "view_only"


# --- selection vs edit when the selected cell moves (cases taken from the real clip) ---------


def test_moving_the_selection_reveals_the_cells_text_but_is_still_a_selection():
    rec = _rec(
        1, 6.3,
        regions=[
            ("Transaction String", "AMZNWEBSVCSIN*AWS53871 | Transaction String"),
            ("DTDCCORPACC7734FEB | 3,500 | Infrastructure | 45,000", "DTDCCORPACC7734FEB | 3,500 | AMZNWEBSVCSIN*AWS53871 | Infrastructure | 45,000"),
        ],
        before=_loc("F18", ""), after=_loc("C18", "AMZNWEBSVCSIN*AWS53871"),
    )
    assert _cls(rec) == "selection"


def test_moving_away_hides_the_cells_text_and_is_still_a_selection():
    rec = _rec(
        1, 8.3, regions=[("AMZNWEBSVCSIN*AWS53871 | Transaction String", "Transaction String")],
        before=_loc("C18", "AMZNWEBSVCSIN*AWS53871"), after=_loc("F18", ""),
    )
    assert _cls(rec) == "selection"


def test_new_text_found_in_neither_cell_is_an_edit_even_if_the_selection_moved():
    rec = _rec(1, 9.0, regions=[("", "45000")], before=_loc("F18", ""), after=_loc("F19", ""))  # typed, then Enter
    assert _cls(rec) == "data_change"


def test_a_paste_that_keeps_the_selection_in_place_is_a_data_change():
    rec = _rec(1, 18.3, regions=[("500 | 4,100", "500 | 45000 | 4,100")], before=_loc("F18", ""), after=_loc("F18", "45000"))
    assert _cls(rec) == "data_change"
