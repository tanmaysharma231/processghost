"""Intent-action alignment builder."""
from __future__ import annotations

from typing import List

from core import config
from core.align.match import match_score
from core.models.tokens import ActionToken, Alignment, IntentToken


def build_alignments(
    actions: List[ActionToken],
    intents: List[IntentToken],
    window: float | None = None,
    tau_low: float | None = None,
    tau_high: float | None = None,
) -> List[Alignment]:
    window = window or config.MATCH_WINDOW_S
    tau_low = tau_low or config.TAU_LOW
    tau_high = tau_high or config.TAU_HIGH

    alignments: List[Alignment] = []
    for action in actions:
        best_score = -1.0
        best_intent = None
        best_reason = ""
        for intent in intents:
            score, reason = match_score(action, intent, window=window)
            if score > best_score:
                best_score = score
                best_intent = intent
                best_reason = reason
        if best_score < tau_low:
            alignments.append(
                Alignment(
                    action_id=action.action_id,
                    intent_id=None,
                    match_score=best_score if best_score >= 0 else 0.0,
                    reason=f"unexplained:{best_reason}",
                    notes="below_tau_low",
                )
            )
        else:
            alignments.append(
                Alignment(
                    action_id=action.action_id,
                    intent_id=best_intent.intent_id if best_intent else None,
                    match_score=best_score,
                    reason=best_reason,
                    notes="uncertain" if best_score < tau_high else "explained",
                )
            )
    return alignments
