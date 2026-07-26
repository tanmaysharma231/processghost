"""Contradiction rule (lightweight placeholder)."""
from __future__ import annotations

from core.models.tokens import Dissonance, Evidence, RunArtifacts

NEGATION_WORDS = {"never", "no", "not", "don't", "doesn't", "won't", "only"}


def rule(artifacts: RunArtifacts) -> list[Dissonance]:
    diss: list[Dissonance] = []
    transcript_text = " ".join(seg.text.lower() for seg in artifacts.transcript)
    if not any(w in transcript_text for w in NEGATION_WORDS):
        return diss
    # placeholder: if contradiction words present and actions exist, raise medium severity
    if artifacts.actions:
        action = artifacts.actions[0]
        diss.append(
            Dissonance(
                dissonance_id="contradiction_1",
                kind="contradiction",
                severity="high",
                t_s=action.t_s,
                title="Possible contradiction",
                explanation="Narration includes absolutes/negations; actions present may contradict. Manual review needed.",
                action_ids=[action.action_id],
                intent_ids=[],
                evidence=Evidence(
                    t_s=action.t_s,
                    frame_paths=[action.frame_path],
                    transcript_excerpt=transcript_text[:200],
                    transcript_range_s=None,
                ),
                rule_id="r_contradiction",
                rule_version="0.1",
            )
        )
    return diss
