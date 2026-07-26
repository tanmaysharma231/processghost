"""Deterministic match scoring between an action and an intent."""
from __future__ import annotations

import re
from typing import Tuple

from core.models.tokens import ActionToken, IntentToken


def _keywords(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[a-zA-Z0-9]+", text)}


def keyword_overlap(a: str, b: str) -> float:
    ka, kb = _keywords(a), _keywords(b)
    if not ka or not kb:
        return 0.0
    inter = len(ka & kb)
    union = len(ka | kb)
    return inter / union


def compatible_type(action: ActionToken, intent: IntentToken) -> float:
    pairs = [
        ("export", "export"),
        ("submit", "submit"),
        ("approve", "approve"),
        ("filter", "filter"),
        ("sort", "sort"),
        ("open", "open"),
        ("login", "login"),
    ]
    for a, i in pairs:
        if a in action.action_type.lower() and i in intent.intent.lower():
            return 0.2
    return 0.0


def time_proximity(action: ActionToken, intent: IntentToken, window: float = 10.0) -> float:
    dt = abs(action.t_s - intent.t_s)
    if dt >= window:
        return 0.0
    return (window - dt) / window * 0.3  # max 0.3 weight


def match_score(action: ActionToken, intent: IntentToken, window: float = 10.0) -> Tuple[float, str]:
    kw = keyword_overlap(action.details or action.target or "", intent.raw_text or intent.intent or "")
    comp = compatible_type(action, intent)
    tp = time_proximity(action, intent, window=window)
    score = min(1.0, kw * 0.6 + comp + tp)
    reason = f"kw={kw:.2f},type={comp:.2f},time={tp:.2f}"
    return score, reason
