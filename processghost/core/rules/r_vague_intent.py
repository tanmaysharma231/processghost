"""Vague Intent rule."""
from __future__ import annotations

from collections import defaultdict

from core import config
from core.models.tokens import Alignment, Dissonance, Evidence, RunArtifacts


def rule(artifacts: RunArtifacts) -> list[Dissonance]:
    diss: list[Dissonance] = []
    intents_by_id = {i.intent_id: i for i in artifacts.intents}
    actions_by_intent: dict[str, list[str]] = defaultdict(list)
    for al in artifacts.alignments:
        if al.intent_id:
            actions_by_intent[al.intent_id].append(al.action_id)

    for intent_id, action_ids in actions_by_intent.items():
        intent = intents_by_id.get(intent_id)
        if not intent or not intent.is_vague:
            continue
        if len(action_ids) >= 3:  # heuristic cluster size
            diss.append(
                Dissonance(
                    dissonance_id=f"vague_{intent_id}",
                    kind="vague_intent",
                    severity="medium",
                    t_s=intent.t_s,
                    title="Vague intent covers multiple actions",
                    explanation="Narration is vague and maps to several distinct actions.",
                    action_ids=action_ids[:3],
                    intent_ids=[intent_id],
                    evidence=Evidence(
                        t_s=intent.t_s,
                        frame_paths=[],
                        transcript_excerpt=intent.raw_text,
                        transcript_range_s=(intent.t_s - config.MATCH_WINDOW_S / 2, intent.t_s + config.MATCH_WINDOW_S / 2),
                    ),
                    rule_id="r_vague_intent",
                    rule_version="0.1",
                )
            )
    return diss
