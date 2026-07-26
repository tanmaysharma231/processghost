"""Parse LLM JSON-mode responses into the pipeline's Pydantic models.

Kept separate from core/pipeline.py so the parsing logic (the part that can actually be wrong --
malformed JSON, a missing field, a type that doesn't validate) is unit-testable against canned
response strings, with no live API call required. Each function raises on bad input; callers in
core/pipeline.py are responsible for catching and falling back to the heuristic path.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.models.tokens import ActionToken, IntentToken


class LLMResponseParseError(ValueError):
    """Raised when an LLM JSON-mode response cannot be parsed into the expected model."""


def extract_chat_content(response: dict) -> str:
    """Pull the assistant message text out of a raw OpenAI chat/vision completion dict."""
    try:
        return response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMResponseParseError(f"Response missing choices[0].message.content: {exc}") from exc


def parse_intent_response(raw_content: str, *, seg: dict) -> IntentToken:
    """Parse an intent-extraction JSON-mode response into an IntentToken.

    `seg` is the raw Whisper segment dict this intent was extracted from -- it supplies the
    fields the model doesn't need to repeat (timing, the original text, an id).
    """
    try:
        data = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise LLMResponseParseError(f"Not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise LLMResponseParseError(f"Expected a JSON object, got {type(data).__name__}")

    text = seg.get("text", "")
    try:
        return IntentToken(
            intent_id=f"intent_{int(seg.get('id', seg['start']))}",
            t_s=float(seg["start"]),
            span_s=float(seg["end"] - seg["start"]),
            raw_text=text,
            intent=str(data.get("intent") or text),
            verb=data.get("verb") or None,
            obj=data.get("obj") or None,
            confidence=float(data.get("confidence", 0.0)),
            is_vague=bool(data.get("is_vague", False)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMResponseParseError(f"Response did not match IntentToken shape: {exc}") from exc


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
