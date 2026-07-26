"""Unresolved Reference rule.

Thin wrapper over artifacts.unresolved_entities, which core/pipeline.py's cross-check call
(core/llm/prompts.py: CROSS_CHECK_SYSTEM) already computes with full-run context -- an entity
reference that was never named specifically AND that nothing observed on screen ever resolved,
anywhere in the run. Replaces the old r_vague_intent rule, which fired on an arbitrary "matched to
>=3 actions" threshold; this is a strictly more precise, generically-derived version of the same
signal (see Process_ghost's plan history for the full reasoning).
"""
from __future__ import annotations

from core.models.tokens import Dissonance, Evidence, RunArtifacts


def rule(artifacts: RunArtifacts) -> list[Dissonance]:
    diss: list[Dissonance] = []
    intents_by_id = {i.intent_id: i for i in artifacts.intents}

    for idx, finding in enumerate(artifacts.unresolved_entities):
        intent = intents_by_id.get(finding.intent_id)
        t_s = intent.t_s if intent else 0.0
        excerpt = intent.raw_text if intent else finding.raw_mention
        diss.append(
            Dissonance(
                dissonance_id=f"unresolved_{idx}_{finding.intent_id}",
                kind="unresolved_reference",
                severity="high",
                t_s=t_s,
                title="Unresolved reference",
                explanation=finding.reason,
                action_ids=[],
                intent_ids=[finding.intent_id],
                evidence=Evidence(
                    t_s=t_s,
                    frame_paths=[],
                    transcript_excerpt=excerpt,
                    transcript_range_s=None,
                    extra={"raw_mention": finding.raw_mention},
                ),
                rule_id="r_unresolved_reference",
                rule_version="0.1",
            )
        )
    return diss
