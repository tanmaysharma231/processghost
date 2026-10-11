"""Unit tests for evals/score_findings.py -- pure text scoring, no pipeline."""
from __future__ import annotations

from evals.score_findings import aggregate, load_spec, score_text

SPEC = load_spec()


def _ids(score):
    return [r["id"] for r in score["findings"] if r["surfaced"]]


def test_empty_output_surfaces_nothing():
    s = score_text("", SPEC)
    assert s["n_surfaced"] == 0 and s["n_required"] == 6 and not s["false_completeness"]


def test_sheet_names_are_found_even_with_ocr_spacing():
    s = score_text("Switch to MonthlyActuals\nthen Transactions-March\nfrom Master Bills", SPEC)
    assert _ids(s) == ["G1", "G2", "G3"]


def test_bill_id_must_appear_with_a_matching_idea_on_the_same_line():
    assert "G4" not in _ids(score_text("The data includes Bill IDs and categories", SPEC))
    assert "G4" in _ids(score_text("How are rows matched? By Bill ID or by vendor name?", SPEC))
    assert "G4" not in _ids(score_text("Bill ID\nmatched somewhere else", SPEC))  # different lines do not count


def test_column_f_by_cell_reference_or_name():
    assert "G5" in _ids(score_text("the cursor lands on F18", SPEC))
    assert "G5" in _ids(score_text("paste into column F", SPEC))
    assert "G5" not in _ids(score_text("under the relevant columns", SPEC))


def test_status_requirement_by_paid_or_status_with_total():
    assert "G6" in _ids(score_text("Status must be Paid", SPEC))
    assert "G6" in _ids(score_text("the status column decides whether the total counts", SPEC))
    assert "G6" not in _ids(score_text("the total updates", SPEC))


def test_forbidden_claims_and_false_completeness():
    s = score_text("A usage spike. Nothing is missing -- every open question was resolved.", SPEC)
    assert s["forbidden_claims"] == ["usage spike"] and s["false_completeness"]
    # a completeness banner is only false if findings are missing
    full = "\n".join(["Master Bills", "Monthly Actuals", "Transactions - March", "match by Bill ID", "F18", "Paid", "Nothing is missing"])
    assert not score_text(full, SPEC)["false_completeness"]


def test_aggregate_hit_rate():
    a, b = score_text("Master Bills", SPEC), score_text("nothing", SPEC)
    assert aggregate([a, b])["G1"] == 0.5 and aggregate([a, b])["G2"] == 0.0
