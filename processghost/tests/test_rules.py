"""Unit tests for the two new/rewritten dissonance rules.

Both r_unresolved_reference and r_unconfirmed_outcome are thin wrappers over pre-computed
cross-check findings (RunArtifacts.unresolved_entities / .unconfirmed_claims) -- no LLM call
needed here, these tests construct RunArtifacts directly.

Includes one small synthetic SECOND scenario in a completely different domain (support tickets,
not spreadsheets) as the anti-overfitting check: the rules must fire on it using the exact same
generic mechanism as the bills-tracker scenario, with no domain-specific code path. If they only
worked for one domain, that would be a sign of accidental overfitting.
"""
from __future__ import annotations

from pathlib import Path

from core.models.tokens import (
    IntentToken,
    RunArtifacts,
    UnconfirmedClaimFinding,
    UnresolvedEntityFinding,
)
from core.rules import r_unconfirmed_outcome, r_unresolved_reference


def _artifacts(**overrides) -> RunArtifacts:
    base = dict(
        run_id="test-run",
        video_path=Path("video.mp4"),
        audio_path=Path("audio.wav"),
        frames_dir=Path("frames"),
    )
    base.update(overrides)
    return RunArtifacts(**base)


def _intent(intent_id: str, t_s: float, raw_text: str) -> IntentToken:
    return IntentToken(
        intent_id=intent_id,
        t_s=t_s,
        span_s=2.0,
        raw_text=raw_text,
        intent=raw_text,
        confidence=0.8,
        is_vague=True,
    )


# --- r_unresolved_reference --------------------------------------------------

def test_r_unresolved_reference_fires_one_dissonance_per_finding():
    intents = [_intent("intent_0", 3.0, "We go to the shared drive.")]
    artifacts = _artifacts(
        intents=intents,
        unresolved_entities=[
            UnresolvedEntityFinding(
                intent_id="intent_0",
                raw_mention="the shared drive",
                reason="never named specifically, no action confirms which drive",
            )
        ],
    )

    diss = r_unresolved_reference.rule(artifacts)

    assert len(diss) == 1
    assert diss[0].kind == "unresolved_reference"
    assert diss[0].t_s == 3.0
    assert diss[0].intent_ids == ["intent_0"]
    assert diss[0].explanation == "never named specifically, no action confirms which drive"
    assert diss[0].evidence.extra == {"raw_mention": "the shared drive"}


def test_r_unresolved_reference_empty_findings_produces_no_dissonances():
    artifacts = _artifacts(intents=[_intent("intent_0", 0.0, "text")], unresolved_entities=[])
    assert r_unresolved_reference.rule(artifacts) == []


def test_r_unresolved_reference_handles_missing_intent_gracefully():
    # finding references an intent_id that isn't in artifacts.intents -- must not crash
    artifacts = _artifacts(
        intents=[],
        unresolved_entities=[UnresolvedEntityFinding(intent_id="intent_missing", raw_mention="x", reason="y")],
    )
    diss = r_unresolved_reference.rule(artifacts)
    assert len(diss) == 1
    assert diss[0].t_s == 0.0


# --- r_unconfirmed_outcome ---------------------------------------------------

def test_r_unconfirmed_outcome_fires_one_dissonance_per_finding():
    intents = [_intent("intent_1", 8.0, "It closes automatically.")]
    artifacts = _artifacts(
        intents=intents,
        unconfirmed_claims=[
            UnconfirmedClaimFinding(
                intent_id="intent_1",
                claimed_outcome="it closes automatically",
                reason="no action observed that closes anything",
            )
        ],
    )

    diss = r_unconfirmed_outcome.rule(artifacts)

    assert len(diss) == 1
    assert diss[0].kind == "unconfirmed_outcome"
    assert diss[0].t_s == 8.0
    assert diss[0].intent_ids == ["intent_1"]
    assert diss[0].evidence.extra == {"claimed_outcome": "it closes automatically"}


def test_r_unconfirmed_outcome_empty_findings_produces_no_dissonances():
    artifacts = _artifacts(intents=[_intent("intent_0", 0.0, "text")], unconfirmed_claims=[])
    assert r_unconfirmed_outcome.rule(artifacts) == []


# --- anti-overfitting: a completely different domain ------------------------

def test_rules_generalize_to_a_different_domain_support_ticket_scenario():
    """Synthetic support-ticket workflow, nothing to do with spreadsheets or bills. Same generic
    mechanism (pre-computed cross-check findings -> one Dissonance each) must fire identically."""
    intents = [
        _intent("intent_0", 1.0, "I go to the queue and grab the next one."),
        _intent("intent_1", 12.0, "Marking it done closes it out for the customer."),
    ]
    artifacts = _artifacts(
        intents=intents,
        unresolved_entities=[
            UnresolvedEntityFinding(
                intent_id="intent_0",
                raw_mention="the queue",
                reason="never named which support queue, no action confirms which one",
            )
        ],
        unconfirmed_claims=[
            UnconfirmedClaimFinding(
                intent_id="intent_1",
                claimed_outcome="closes it out for the customer",
                reason="no action observed that sends a customer-facing closure notice",
            )
        ],
    )

    unresolved_diss = r_unresolved_reference.rule(artifacts)
    unconfirmed_diss = r_unconfirmed_outcome.rule(artifacts)

    assert len(unresolved_diss) == 1
    assert unresolved_diss[0].kind == "unresolved_reference"
    assert unresolved_diss[0].t_s == 1.0

    assert len(unconfirmed_diss) == 1
    assert unconfirmed_diss[0].kind == "unconfirmed_outcome"
    assert unconfirmed_diss[0].t_s == 12.0
