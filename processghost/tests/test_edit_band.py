"""The formula-bar band: found once, watched as a second signal so a small text edit becomes an event, and the settled
after-frame. Synthetic clips and fake OCR: no model, no real recordings."""
from __future__ import annotations

import cv2
import numpy as np

from core import config
from core.screen.edit_band import BandWatch, band_changed_frac, find_edit_band, text_changed
from core.screen.timeline import build_screen_timeline
from tests.test_screen_timeline import H, W, _white, _with_rects, _write_video

GLYPH_ROW = (22, 38)  # y0, y1 of the fake formula-bar glyph


def _lines(_img):
    return [("fx", (5, GLYPH_ROW[0], 20, GLYPH_ROW[1])), ("Find", (200, 150, 230, 160))]


def _ocr(img):
    """Fake OCR: any dark pixel in the crop reads as text, so a patch appearing in the band is a text change."""
    return "T" if (img < 128).any() else ""


def _with_patch(x=100, y=25, size=3):
    img = _white()
    img[y : y + size, x : x + size] = 0  # 9 pixels: far below SCREEN_ACTIVE_FRAC of the frame, a visible share of the band
    return img


# --- finding the band ----------------------------------------------------------------------------------------------------


def test_the_band_is_the_glyph_row_with_padding():
    assert find_edit_band([_white()], _lines) == (GLYPH_ROW[0] - config.SCREEN_BAND_PAD_PX, GLYPH_ROW[1] + config.SCREEN_BAND_PAD_PX)


def test_no_glyph_or_a_glyph_far_to_the_right_means_no_band():
    assert find_edit_band([_white()], lambda img: [("Find", (5, 22, 40, 38))]) is None
    assert find_edit_band([_white()], lambda img: [("fx", (W - 40, 22, W - 10, 38))]) is None


def test_the_next_early_frame_is_tried_when_the_first_has_no_glyph():
    calls = []

    def lines(img):
        calls.append(1)
        return [] if len(calls) == 1 else _lines(img)

    assert find_edit_band([_white(), _white()], lines) is not None


# --- watching it ---------------------------------------------------------------------------------------------------------


def test_only_changes_inside_the_band_count_for_the_band():
    band = (14, 46)
    assert band_changed_frac(_white(), _white(), band) == 0.0
    assert band_changed_frac(_white(), _with_patch(y=25), band) > config.SCREEN_BAND_PREFILTER_FRAC
    assert band_changed_frac(_white(), _with_patch(y=120), band) == 0.0  # a change elsewhere on the screen is not the band's


def test_text_that_differs_is_a_change_and_ocr_jitter_is_not():
    assert text_changed("ab", "abcd") and text_changed("Pending", "Accepted")
    assert not text_changed("Pending", "Pending ") and not text_changed("a | b", "ab")


def test_an_idle_band_costs_no_ocr_and_a_moved_pointer_is_not_a_text_change():
    calls = []

    def ocr(img):
        calls.append(1)
        return "same text"

    watch = BandWatch([_white(), _white(), _with_patch()], (14, 46), ocr)
    assert watch.changed(1) is False and calls == []  # nothing moved: nothing read
    assert watch.changed(2) is False and len(calls) == 2  # pixels moved, but the text read is the same


def test_a_text_change_is_seen_and_the_ocr_budget_stops_the_signal(monkeypatch):
    watch = BandWatch([_white(), _with_patch()], (14, 46), _ocr)
    assert watch.changed(1) is True
    monkeypatch.setattr(config, "SCREEN_BAND_MAX_OCR", 0)
    assert BandWatch([_white(), _with_patch()], (14, 46), _ocr).changed(1) is False


# --- in the timeline -----------------------------------------------------------------------------------------------------


def test_a_few_characters_edited_in_the_band_become_an_event_only_when_the_band_is_watched(tmp_path):
    video = _write_video(tmp_path / "v.mp4", [(2.0, _white), (3.0, _with_patch)])
    plain = build_screen_timeline(video, tmp_path / "plain", ocr=_ocr)  # no ocr_lines: no band, the old behaviour
    watched = build_screen_timeline(video, tmp_path / "watched", ocr=_ocr, ocr_lines=_lines)
    assert plain.events == [] and plain.edit_band is None
    assert watched.edit_band == (14, 46) and len(watched.events) == 1
    assert 1.5 <= watched.events[0].t_start <= 2.6


def test_the_after_frame_is_the_settled_one_but_never_past_the_next_event(tmp_path):
    # A big change at 2s, then a tiny edit (under the whole-frame threshold) a second later, which only the settled frame shows.
    video = _write_video(tmp_path / "v.mp4", [(2.0, _white), (1.0, lambda: _with_rects((100, 60, 60, 30))), (3.0, lambda: _with_rects((100, 60, 60, 30), (10, 150, 2, 2)))])
    tl = build_screen_timeline(video, tmp_path / "out", ocr=_no_ocr)
    assert len(tl.events) == 1
    after = cv2.imread(str(tmp_path / "out" / tl.events[0].after_frame))
    assert after[151, 11].max() < 128  # the late tiny patch is on the settled frame
    assert tl.events[0].t_end < 2.7  # the event itself still ends at its last changed sample


def test_a_following_event_bounds_the_settled_frame(tmp_path):
    video = _write_video(tmp_path / "v.mp4", [(2.0, _white), (1.0, lambda: _with_rects((100, 60, 60, 30))), (3.0, lambda: _with_rects((100, 60, 60, 30), (200, 100, 80, 50)))])
    tl = build_screen_timeline(video, tmp_path / "out", ocr=_no_ocr)
    assert len(tl.events) == 2
    first_after = cv2.imread(str(tmp_path / "out" / tl.events[0].after_frame))
    assert first_after[120, 240].min() > 200  # the second action's box is not on the first event's after-frame
    assert np.array_equal(first_after, cv2.imread(str(tmp_path / "out" / tl.events[1].before_frame)))


def _no_ocr(_img):
    return ""
