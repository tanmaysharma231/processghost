"""Unit tests for evals/score_findings.py -- pure text scoring, no pipeline."""
from __future__ import annotations

from evals.score_findings import aggregate, load_spec, score_text

SPEC = load_spec()


def _ids(score):
    return [r["id"] for r in score["findings"] if r["surfaced"]]


def test_empty_output_surfaces_nothing():
    s = score_text("", SPEC)
    assert s["n_surfaced"] == 0 and s["n_required"] == len(SPEC["findings"]) and not s["false_completeness"]


def test_sheet_names_are_found_even_with_ocr_spacing():
    s = score_text("Switch to MonthlyActuals\nthen Transactions-March\nfrom Master Bills", SPEC)
    assert _ids(s) == ["G1", "G2", "G3"]


def test_the_row_identifier_must_be_named_with_a_matching_idea_on_the_same_line():
    assert "G4" not in _ids(score_text("The data includes a Transaction String and categories", SPEC))
    assert "G4" in _ids(score_text("How is the row identified? By its Transaction String?", SPEC))
    assert "G4" not in _ids(score_text("Transaction String" + chr(10) + "identified somewhere else", SPEC))  # different lines do not count
    assert "G4" not in _ids(score_text("Rows are matched by Bill ID", SPEC))  # the owner says Bill ID repeats: not the identifier


def test_the_search_column_needs_a_column_and_a_search_idea_on_one_line():
    assert "G5" in _ids(score_text("Search column B of the transactions sheet for that string", SPEC))
    assert "G5" not in _ids(score_text("the sheet has a column B", SPEC))


def test_the_value_taken_is_the_actualized_amount_in_column_c():
    assert "G6" in _ids(score_text("Copy the Actualized Amount of the matching row", SPEC))
    assert "G6" in _ids(score_text("the value in column C is copied", SPEC))
    assert "G6" not in _ids(score_text("the amount is large", SPEC))


def test_column_f_needs_a_paste_target_not_just_a_cell_mention():
    assert "G7" in _ids(score_text("paste the amount into F18", SPEC))
    assert "G7" in _ids(score_text("paste into column F", SPEC))
    assert "G7" in _ids(score_text("- Target: Monthly Actuals!F18 (Actual)", SPEC))  # a stated target counts
    assert "G7" in _ids(score_text("the Actual column (column F) holds the amount", SPEC))
    assert "G7" not in _ids(score_text("the sheet changed and the focus moved to cell F18", SPEC))  # real run: observation only
    assert "G7" not in _ids(score_text("under the relevant columns", SPEC))


def test_repetition_for_every_other_transaction_is_a_finding():
    assert "G8" in _ids(score_text("Repeat for every other transaction in the period", SPEC))
    assert "G8" not in _ids(score_text("Copy the amount", SPEC))


def test_variance_must_be_called_a_formula_or_protected():
    assert "G9" in _ids(score_text("Variance is a formula that calculates the difference", SPEC))
    assert "G9" not in _ids(score_text("The Variance column is on the right", SPEC))


def test_the_paid_requirement_needs_status_and_paid_and_not_the_status_bar():
    assert "G10" in _ids(score_text("The total counts a row only where H18:H25 equals 'Paid'", SPEC))
    assert "G10" in _ids(score_text("Status must be Paid for the total", SPEC))
    assert "G10" not in _ids(score_text("Step 9: sum in status bar matches total value on screen, bill paid", SPEC))


def test_forbidden_claims_and_false_completeness():
    s = score_text("A usage spike. Nothing is missing -- every open question was resolved.", SPEC)
    assert s["forbidden_claims"] == ["usage spike"] and s["false_completeness"]
    # a completeness banner is only false if findings are missing
    full = "\n".join([
        "Master Bills", "Monthly Actuals", "Transactions - March", "the row is identified by its Transaction String",
        "search column B for the Transaction String", "copy the Actualized Amount", "paste into F18",
        "repeat for every other transaction", "Variance is a formula", "Status must be Paid", "Nothing is missing",
    ])
    assert not score_text(full, SPEC)["false_completeness"]


def test_the_billed_total_is_a_real_number_the_error_is_misattributing_it():
    assert score_text("The billed total is 235,000 on screen", SPEC)["forbidden_claims"] == []
    assert score_text("the pasted value contributes to the total of 235,000", SPEC)["forbidden_claims"] != []


def test_aggregate_hit_rate():
    a, b = score_text("Master Bills", SPEC), score_text("nothing", SPEC)
    assert aggregate([a, b])["G1"] == 0.5 and aggregate([a, b])["G2"] == 0.0
