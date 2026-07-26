"""Rule engine driver."""
from __future__ import annotations

from typing import Callable, Iterable, List, Protocol

from core.models.tokens import Dissonance, RunArtifacts


class DissonanceRule(Protocol):
    rule_id: str
    rule_version: str

    def apply(self, artifacts: RunArtifacts) -> List[Dissonance]:
        ...


def run_rules(artifacts: RunArtifacts, rules: Iterable[Callable[[RunArtifacts], List[Dissonance]]]) -> RunArtifacts:
    dissonances: List[Dissonance] = []
    for rule in rules:
        try:
            dissonances.extend(rule(artifacts))
        except Exception as exc:  # pragma: no cover - defensive
            dissonances.append(
                Dissonance(
                    dissonance_id=f"rule_error_{rule.__name__}",
                    kind="rule_error",
                    severity="low",
                    t_s=0.0,
                    title="Rule execution error",
                    explanation=str(exc),
                    action_ids=[],
                    intent_ids=[],
                    evidence=None,  # type: ignore
                    rule_id=rule.__name__,
                    rule_version="error",
                )
            )
    artifacts.dissonances = dedupe_dissonances(dissonances)
    return artifacts


def dedupe_dissonances(items: List[Dissonance]) -> List[Dissonance]:
    seen = set()
    deduped: List[Dissonance] = []
    for d in items:
        key = (d.kind, d.t_s, tuple(sorted(d.action_ids)), tuple(sorted(d.intent_ids)))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(d)
    return deduped
