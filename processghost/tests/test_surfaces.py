"""Surface locators (ADR 0013) on fake chrome strings. Spreadsheet strings follow the structure of real
spreadsheet chrome; the WEB strings are synthetic and prove nothing about real browsers until a real web
recording exists."""
from __future__ import annotations

from core.models.screen import Locator
from core.screen.surfaces import diff_locators, locate

SHEET_A = "Budget.xlsx - LibreOffice Calc | C7 | fx | 4500 | Sheet 1 of 3 | PageStyle_Budget | Average:4,500;Sum:4,500"
SHEET_A_OTHER_CELL = "Budget.xlsx - LibreOffice Calc | D9 | fx | =SUM(D1:D8) | Sheet 1 of 3 | PageStyle_Budget"
SHEET_B = "Budget.xlsx - LibreOffice Calc | A1 | fx | Vendor | Sheet 2 of 3 | PageStyle_Actuals"
OTHER_BOOK = "Plan.xlsx - LibreOffice Calc | A1 | fx | x | Sheet 1 of 1 | PageStyle_Plan"
EMPTY_CELL = "Budget.xlsx | B2 | fx | Sheet 1 of 3 | PageStyle_Budget"
WEB_A = "Orders - Google Chrome | https://erp.example.com/orders/list | Home > Orders > List"
WEB_B = "New order - Google Chrome | https://erp.example.com/orders/new | Home > Orders > New"
WEB_A_SAME_PAGE = "Orders - Google Chrome | https://erp.example.com/orders/list | Home > Orders > List | filter applied"


def test_spreadsheet_locator_fields():
    loc = locate(SHEET_A)
    assert loc.surface == "spreadsheet"
    assert loc.focus == "C7" and loc.focus_content == "4500"
    assert loc.location == "Budget" and loc.index == 1
    assert loc.document == "Budget.xlsx" and loc.app == "LibreOffice Calc"
    assert loc.confidence >= 0.9


def test_every_spreadsheet_evidence_string_is_in_the_chrome_text():
    loc = locate(SHEET_A)
    assert loc.evidence and all(e in SHEET_A for e in loc.evidence)


def test_empty_cell_does_not_take_the_status_bar_as_formula_content():
    assert locate(EMPTY_CELL).focus_content == ""


def test_same_sheet_different_cell_is_a_same_surface_edit():
    assert diff_locators(locate(SHEET_A), locate(SHEET_A_OTHER_CELL)) == "same_surface_edit"


def test_sheet_switch_is_detected_from_the_sheet_index():
    assert diff_locators(locate(SHEET_A), locate(SHEET_B)) == "sheet_switch"


def test_sheet_index_beats_a_misread_sheet_name():
    garbled = SHEET_A.replace("PageStyle_Budget", "PageStyle_Budqet")
    assert diff_locators(locate(SHEET_A), locate(garbled)) == "same_surface_edit"


def test_different_workbook_is_a_document_switch():
    assert diff_locators(locate(SHEET_A), locate(OTHER_BOOK)) == "document_switch"


def test_web_locator_fields():
    loc = locate(WEB_A)
    assert loc.surface == "web"
    assert loc.document == "erp.example.com" and loc.location == "/orders/list"
    assert loc.app == "Google Chrome" and loc.title == "Orders"


def test_page_navigation_is_detected():
    assert diff_locators(locate(WEB_A), locate(WEB_B)) == "page_navigation"


def test_same_web_page_is_a_same_surface_edit():
    assert diff_locators(locate(WEB_A), locate(WEB_A_SAME_PAGE)) == "same_surface_edit"


def test_spreadsheet_to_browser_is_an_app_switch():
    assert diff_locators(locate(SHEET_A), locate(WEB_A)) == "app_switch"


def test_file_name_is_not_mistaken_for_a_url():
    assert locate("Budget.xlsx | notes.txt").surface == "generic"


def test_unrecognised_chrome_falls_back_to_generic_and_never_claims_a_change():
    a, b = locate("Some Tool 1.0 | menu"), locate("Another Tool | menu")
    assert a.surface == "generic" and b.surface == "generic"
    assert diff_locators(a, b) == "unknown"
    assert diff_locators(a, locate("Some Tool 1.0 | other")) == "same_surface_edit"


def test_generic_next_to_specific_is_unknown_not_a_switch():
    assert diff_locators(locate("blurry"), locate(SHEET_A)) == "unknown"


def test_missing_locator_is_unknown():
    assert diff_locators(None, Locator()) == "unknown"
    assert diff_locators(locate(SHEET_A), None) == "unknown"


# --- OCR robustness: the formula-bar glyph is often garbled or dropped ---------------------


def test_garbled_formula_bar_glyph_still_gives_the_cell_and_skips_the_debris():
    loc = locate("Arial | 10pt | C18 | fx>?= | ItemCode9 | Find | FindAll | Sheet2of3 | PageStyle_Actuals")
    assert loc.focus == "C18" and loc.focus_content == "ItemCode9" and loc.index == 2


def test_cell_reference_without_any_glyph_gives_focus_but_no_content():
    loc = locate("Arial | 10pt | F18 | Find | FindAll | Sheet2of3 | PageStyle_Actuals | Sum:0")
    assert loc.focus == "F18" and loc.focus_content == ""
    assert all(e in "Arial | 10pt | F18 | Find | FindAll | Sheet2of3 | PageStyle_Actuals | Sum:0" for e in loc.evidence)


def test_empty_cell_does_not_take_toolbar_words_as_content():
    assert locate("B2 | fx | Find | FindAll | Sheet 1 of 3 | PageStyle_Budget").focus_content == ""


def test_font_and_zoom_fragments_are_not_cell_references():
    assert locate("Arial | 10pt | 100% | Sheet 1 of 3 | PageStyle_Budget").focus == ""
