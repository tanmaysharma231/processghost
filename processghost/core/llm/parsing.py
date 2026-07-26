"""Parse LLM JSON-mode responses into the pipeline's Pydantic models.

Kept separate from core/pipeline.py so the parsing logic (the part that can actually be wrong --
malformed JSON, a missing field, a type that doesn't validate) is unit-testable against canned
response strings, with no live API call required. Each function raises on bad input; callers in
core/pipeline.py are responsible for catching and falling back to the heuristic path.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

from core.models.tokens import (
    ActionToken,
    EntityReference,
    IntentToken,
    UnconfirmedClaimFinding,
    UnresolvedEntityFinding,
)


class LLMResponseParseError(ValueError):
    """Raised when an LLM JSON-mode response cannot be parsed into the expected model."""


def extract_chat_content(response: dict) -> str:
    """Pull the assistant message text out of a raw OpenAI chat/vision completion dict."""
    try:
        return response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMResponseParseError(f"Response missing choices[0].message.content: {exc}") from exc


def _parse_entity_references(raw_list) -> List[EntityReference]:
    if raw_list is None:
        return []
    if not isinstance(raw_list, list):
        raise LLMResponseParseError(f"entities_referenced must be a list, got {type(raw_list).__name__}")
    refs = []
    for item in raw_list:
        if not isinstance(item, dict):
            raise LLMResponseParseError(f"entities_referenced item must be an object, got {type(item).__name__}")
        refs.append(
            EntityReference(
                raw_mention=str(item.get("raw_mention", "")),
                entity_role=str(item.get("entity_role") or "other"),
                named_specifically=bool(item.get("named_specifically", False)),
            )
        )
    return refs


def parse_transcript_extraction_response(raw_content: str, segments: List[dict]) -> List[IntentToken]:
    """Parse a whole-transcript extraction JSON-mode response into one IntentToken per segment.

    The response must be a JSON object with a "segments" array containing exactly one item per
    input segment, in the same order (mapped positionally via zip -- `segment_index` in each item
    is informational only, not relied on for correctness). Raises on any structural problem --
    wrong length, wrong type, a field that doesn't validate -- so the caller in core/pipeline.py
    can fall back to the heuristic path for the whole batch rather than accept a partially-wrong
    result.
    """
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise LLMResponseParseError(f"Not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise LLMResponseParseError(f"Expected a JSON object, got {type(parsed).__name__}")

    data = parsed.get("segments")
    if not isinstance(data, list):
        raise LLMResponseParseError(f"Expected 'segments' to be an array, got {type(data).__name__}")
    if len(data) != len(segments):
        raise LLMResponseParseError(f"Expected {len(segments)} items, got {len(data)}")

    tokens: List[IntentToken] = []
    for item, seg in zip(data, segments):
        if not isinstance(item, dict):
            raise LLMResponseParseError(f"Array item must be an object, got {type(item).__name__}")
        text = seg.get("text", "")
        try:
            tokens.append(
                IntentToken(
                    intent_id=f"intent_{int(seg.get('id', seg['start']))}",
                    t_s=float(seg["start"]),
                    span_s=float(seg["end"] - seg["start"]),
                    raw_text=text,
                    intent=str(item.get("intent") or text),
                    verb=item.get("verb") or None,
                    obj=item.get("obj") or None,
                    confidence=float(item.get("confidence", 0.0)),
                    is_vague=bool(item.get("is_vague", False)),
                    entities_referenced=_parse_entity_references(item.get("entities_referenced")),
                    selection_criteria=item.get("selection_criteria") or None,
                    claimed_outcome=item.get("claimed_outcome") or None,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMResponseParseError(f"Response item did not match IntentToken shape: {exc}") from exc
    return tokens


def parse_cross_check_response(
    raw_content: str,
) -> Tuple[List[UnresolvedEntityFinding], List[UnconfirmedClaimFinding]]:
    """Parse a cross-check JSON-mode response into (unresolved_entities, unconfirmed_claims)."""
    try:
        data = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise LLMResponseParseError(f"Not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise LLMResponseParseError(f"Expected a JSON object, got {type(data).__name__}")

    try:
        unresolved = [
            UnresolvedEntityFinding(
                intent_id=str(item["intent_id"]),
                raw_mention=str(item.get("raw_mention", "")),
                reason=str(item.get("reason", "")),
            )
            for item in (data.get("unresolved_entities") or [])
        ]
        unconfirmed = [
            UnconfirmedClaimFinding(
                intent_id=str(item["intent_id"]),
                claimed_outcome=str(item.get("claimed_outcome", "")),
                reason=str(item.get("reason", "")),
            )
            for item in (data.get("unconfirmed_claims") or [])
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMResponseParseError(f"Response did not match cross-check shape: {exc}") from exc

    return unresolved, unconfirmed


def parse_action_response(raw_content: str, *, idx: int, frame_path: Path, t_s: float) -> ActionToken:
    """Parse an action-description JSON-mode vision response into an ActionToken."""
    try:
        data = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise LLMResponseParseError(f"Not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise LLMResponseParseError(f"Expected a JSON object, got {type(data).__name__}")

    try:
        return ActionToken(
            action_id=f"action_{idx:04d}",
            t_s=t_s,
            frame_path=frame_path,
            action_type=str(data.get("action_type") or "unknown"),
            target=data.get("target") or None,
            details=data.get("details") or "",
            app=data.get("app") or None,
            confidence=float(data.get("confidence", 0.0)),
            roi=None,
        )
    except (TypeError, ValueError) as exc:
        raise LLMResponseParseError(f"Response did not match ActionToken shape: {exc}") from exc
