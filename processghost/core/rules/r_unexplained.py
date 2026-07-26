"""Unexplained Action rule."""
from __future__ import annotations

from core import config
from core.models.tokens import Alignment, Dissonance, Evidence, RunArtifacts


def rule(artifacts: RunArtifacts) -> list[Dissonance]:
    diss: list[Dissonance] = []
    align_index = {a.action_id: a for a in artifacts.alignments}
    actions = {a.action_id: a for a in artifacts.actions}
    transcript_text = " ".join(seg.text for seg in artifacts.transcript)

    for action_id, alignment in align_index.items():
        if alignment.intent_id is None and alignment.match_score < (config.TAU_LOW):
            action = actions[action_id]
            excerpt = transcript_text[:200]
            diss.append(
                Dissonance(
                    dissonance_id=f"unexp_{action_id}",
                    kind="unexplained_action",
                    severity="high" if action.action_type in critical_actions() else "medium",
                    t_s=action.t_s,
                    title="Unexplained action",
                    explanation="Action observed without matching intent in narration.",
                    action_ids=[action_id],
                    intent_ids=[],
                    evidence=Evidence(
                        t_s=action.t_s,
                        frame_paths=[action.frame_path],
                        transcript_excerpt=excerpt,
                        transcript_range_s=None,
                    ),
                    rule_id="r_unexplained",
                    rule_version="0.1",
                )
            )
    return diss


def critical_actions() -> set[str]:
    return {
        "open_file",
        "open_sheet",
        "switch_tab",
        "filter",
        "sort",
        "edit_value",
        "export",
        "submit",
        "approve",
        "login",
        "permission_change",
        "delete",
    }
