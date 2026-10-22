"""Evidence-calibrated finding kinds: deterministic, and never stronger than the evidence."""
from __future__ import annotations

from core.models.sop import Attribution, Flag, SOPDocument, SOPStep
from report.build_spec import build_spec, finding_kind


def _flag(**kw) -> Flag:
    base = dict(flag_id="FLAG-01", family="ambiguity", question="q", severity="caution")
    base.update(kw)
    return Flag(**base)


def test_claim_not_shown_is_unsupported_not_disproved():
    f = _flag(status="unresolvable", origin="reconcile", finding_kind="unsupported_assertion")
    assert finding_kind(f) == "unsupported_assertion"


def test_unspecified_rule_is_an_unresolved_requirement():
    f = _flag(status="unresolvable", origin="reconcile", finding_kind="unresolved_requirement")
    assert finding_kind(f) == "unresolved_requirement"
    assert finding_kind(_flag(status="open")) == "unresolved_requirement"  # reviewer ambiguity


def test_unnarrated_screen_change_is_observed_but_unexplained():
    assert finding_kind(_flag(family="structural", origin="screen")) == "observed_unexplained"
    assert finding_kind(_flag(family="structural", origin="reconcile", finding_kind="observed_unexplained")) == "observed_unexplained"


def test_contradiction_is_a_potential_conflict_to_ask_about():
    assert finding_kind(_flag(status="contradicted", finding_kind="unsupported_assertion")) == "potential_conflict"


def test_resolved_flags_are_explained_or_observed():
    assert finding_kind(_flag(status="resolved")) == "explained_action"
    assert finding_kind(_flag(family="structural", status="resolved")) == "observed_unexplained"
    assert finding_kind(_flag(status="resolved", finding_kind="unsupported_assertion")) == "explained_action"


def test_pipeline_limits_are_not_findings_about_the_process():
    assert finding_kind(_flag(status="unchecked", finding_kind="unsupported_assertion")) == "no_finding"
    assert finding_kind(_flag(status="check_failed")) == "no_finding"


def test_build_spec_carries_the_kind():
    doc = SOPDocument(
        run_id="r",
        steps=[SOPStep(step_id="s1", order=1, title="t", instruction="i", attributions=[Attribution(kind="said", t_s=0.0, segment_ids=[0])])],
        flags=[_flag(status="unresolvable", origin="reconcile", finding_kind="unsupported_assertion", window_time_range=(0.0, 3.0))],
    )
    assert build_spec(doc)["flags"][0]["finding_kind"] == "unsupported_assertion"
