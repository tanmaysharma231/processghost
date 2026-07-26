"""Unconfirmed Outcome rule.

Thin wrapper over artifacts.unconfirmed_claims, which core/pipeline.py's cross-check call
(core/llm/prompts.py: CROSS_CHECK_SYSTEM) already computes with full-run context -- narration
asserted an outcome (especially one described as automatic or as a consequence of another step)
that no observed action ever confirmed, anywhere in the run.

This is the mirror image of r_unexplained (an action nothing narrated); this is an intent claiming
something nothing observed backs up. No such rule existed before -- a purely deterministic
keyword+time-proximity aligner (core/align/match.py) cannot tell a coincidentally-nearby action
from one that actually fulfills a specific claim, which is exactly why this needs the cross-check
call's semantic judgment rather than another threshold-based heuristic.
"""
from __future__ import annotations

from core.models.tokens import Dissonance, Evidence, RunArtifacts


def rule(artifacts: RunArtifacts) -> list[Dissonance]:
    diss: list[Dissonance] = []
    intents_by_id = {i.intent_id: i for i in artifacts.intents}

    for idx, finding in enumerate(artifacts.unconfirmed_claims):
        intent = intents_by_id.get(finding.intent_id)
        t_s = intent.t_s if intent else 0.0
        excerpt = intent.raw_text if intent else finding.claimed_outcome
        diss.append(
            Dissonance(
                dissonance_id=f"unconfirmed_{idx}_{finding.intent_id}",
                kind="unconfirmed_outcome",
                severity="high",
                t_s=t_s,
                title="Unconfirmed outcome",
                explanation=finding.reason,
                action_ids=[],
                intent_ids=[finding.intent_id],
                evidence=Evidence(
                    t_s=t_s,
                    frame_paths=[],
                    transcript_excerpt=excerpt,
                    transcript_range_s=None,
                    extra={"claimed_outcome": finding.claimed_outcome},
                ),
                rule_id="r_unconfirmed_outcome",
                rule_version="0.1",
            )
        )
    return diss
