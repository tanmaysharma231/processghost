"""Silent Step rule."""
from __future__ import annotations

from core.models.tokens import Dissonance, Evidence, RunArtifacts


def rule(artifacts: RunArtifacts) -> list[Dissonance]:
    diss: list[Dissonance] = []
    if not artifacts.actions:
        return diss

    # naive heuristic: if actions exist but transcript is very short
    total_transcript = " ".join(seg.text for seg in artifacts.transcript)
    if len(total_transcript.strip()) < 40 and len(artifacts.actions) >= 3:
        for action in artifacts.actions[:3]:
            diss.append(
                Dissonance(
                    dissonance_id=f"silent_{action.action_id}",
                    kind="silent_step",
                    severity="medium",
                    t_s=action.t_s,
                    title="Silent steps detected",
                    explanation="Actions observed while narration is minimal.",
                    action_ids=[action.action_id],
                    intent_ids=[],
                    evidence=Evidence(
                        t_s=action.t_s,
                        frame_paths=[action.frame_path],
                        transcript_excerpt=total_transcript[:200],
                        transcript_range_s=None,
                    ),
                    rule_id="r_silent_step",
                    rule_version="0.1",
                )
            )
    return diss
