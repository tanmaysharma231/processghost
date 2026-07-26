"""Teleportation rule."""
from __future__ import annotations

from core.models.tokens import Dissonance, Evidence, RunArtifacts


TERMINAL_ACTIONS = {"export", "submit", "approve"}
PREREQ_ACTIONS = {"open", "open_file", "open_sheet", "filter", "sort", "edit_value"}


def rule(artifacts: RunArtifacts) -> list[Dissonance]:
    diss: list[Dissonance] = []
    for action in artifacts.actions:
        if any(t in action.action_type for t in TERMINAL_ACTIONS):
            prereq_present = any(
                any(t in a.action_type for t in PREREQ_ACTIONS) for a in artifacts.actions if a.t_s <= action.t_s
            )
            if not prereq_present:
                diss.append(
                    Dissonance(
                        dissonance_id=f"tele_{action.action_id}",
                        kind="teleportation",
                        severity="high",
                        t_s=action.t_s,
                        title="Terminal action without prerequisite",
                        explanation="Export/submit/approve occurred without prerequisite navigation or edits.",
                        action_ids=[action.action_id],
                        intent_ids=[],
                        evidence=Evidence(
                            t_s=action.t_s,
                            frame_paths=[action.frame_path],
                            transcript_excerpt="",
                            transcript_range_s=None,
                        ),
                        rule_id="r_teleportation",
                        rule_version="0.1",
                    )
                )
    return diss
