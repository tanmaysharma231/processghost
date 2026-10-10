"""Structural enforcement of "scenario facts live only in tests, never in prompts" -- see
docs/12-real-scenario-target.md and the v2 plan. These strings are the real grounding scenario's
answers (which sheet, which column, which status value); if any of them ever appears in a prompt
constant, the prompt has stopped being a genuine test of whether the model can find the answer and
started being a template that hands it over.

Covers every prompt in core/llm/prompts.py -- the v1 prompts that used to need excluding here
(they baked in "Monthly Actuals"/"Column F" as illustrative examples, coincidentally overlapping
the real grounding scenario's actual answers) were deleted in the Phase 3 cleanup along with the
pipeline that called them.
"""
from __future__ import annotations

from core.llm import prompts

SCENARIO_STRINGS = [
    "Column F",
    "Bill ID",
    "Paid",
    "Master Bills",
    "Monthly Actuals",
    "Transactions - March",
    "Amazon Web Services",
    "AWS",
    "Variance",
    "SUMIFS",
]

def _prompt_names():
    """Every top-level string constant in core/llm/prompts.py -- no hardcoded list to keep in sync
    as prompts are added, now that the module holds only the real v2 trio."""
    return [name for name, value in vars(prompts).items() if name.isupper() and isinstance(value, str)]


def test_no_prompt_contains_scenario_specifics():
    offenders = []
    for name in _prompt_names():
        value = getattr(prompts, name)
        for s in SCENARIO_STRINGS:
            if s.lower() in value.lower():
                offenders.append((name, s))
    assert offenders == [], f"scenario-specific strings found in prompts: {offenders}"


def test_prompt_names_cover_the_known_v2_trio():
    """Guards against _prompt_names() silently finding zero prompts (e.g. a naming convention
    change) and the scenario-leak test above passing vacuously."""
    assert {"WRITER_SYSTEM", "REVIEWER_SYSTEM", "CHECKER_SYSTEM"} <= set(_prompt_names())
