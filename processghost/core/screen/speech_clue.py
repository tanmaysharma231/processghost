"""Speech clue (ADR 0013): did the speaker name the place a screen event moved to, near the time it
happened? Corroboration only, never a gate. Narration lagged actions by 4-5 s on the measured clip and
some navigation was never narrated, so "no clue" is a signal (possible silent navigation), not a verdict.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from core import config
from core.models.screen import ChangeRecord, Locator, SpeechClue

_SWITCH_KINDS = {"app_switch", "document_switch", "sheet_switch", "page_navigation"}
_NAV_WORDS = {"go", "open", "switch", "move", "click", "navigate", "jump", "back", "next", "tab", "sheet", "page", "screen"}
_STOP_TOKENS = {"sheet", "page", "the", "and", "for", "style", "com", "www", "http", "https"}
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_SPLIT = re.compile(r"[^A-Za-z]+")


def locator_tokens(loc: Optional[Locator]) -> List[str]:
    """Words that would name this place aloud: the sheet name or page path/title, split on CamelCase,
    underscores and punctuation. Digits are dropped: 'Sheet2' says nothing a speaker would name."""
    if loc is None:
        return []
    tokens: List[str] = []
    for text in (loc.location, loc.title):
        for part in _SPLIT.split(_CAMEL.sub(" ", text)):
            part = part.lower()
            if len(part) >= 3 and part not in _STOP_TOKENS and part not in tokens:
                tokens.append(part)
    return tokens


def timed_words(segments_raw: List[dict]) -> List[Tuple[str, float]]:
    out: List[Tuple[str, float]] = []
    for seg in segments_raw:
        for w in seg.get("words") or []:
            clean = re.sub(r"[^a-z0-9]", "", str(w.get("word", "")).lower())
            if clean:
                out.append((clean, float(w.get("start", 0.0))))
    return out


def _names(token: str, word: str) -> bool:
    return token in word or word in token and len(word) >= 4 or SequenceMatcher(None, token, word).ratio() >= config.SURFACE_SPEECH_MIN_RATIO


def speech_clue_for(rec: ChangeRecord, words: List[Tuple[str, float]]) -> Optional[SpeechClue]:
    """None when the event did not change surface (nothing to name)."""
    if rec.surface_change not in _SWITCH_KINDS:
        return None
    lo = rec.t_start - config.SURFACE_SPEECH_WINDOW_BEFORE_S
    hi = rec.t_end + config.SURFACE_SPEECH_WINDOW_AFTER_S
    heard = [(w, t) for w, t in words if lo <= t <= hi]
    tokens = locator_tokens(rec.surface_after)
    matched = [(w, t) for w, t in heard if any(_names(tok, w) for tok in tokens)]
    return SpeechClue(
        found=bool(matched),
        matched=[w for w, _ in matched],
        nav_words=sorted({w for w, _ in heard if w in _NAV_WORDS}),
        lag_s=round(matched[0][1] - rec.t_start, 2) if matched else None,
    )


def attach_speech_clues(records: List[ChangeRecord], segments_raw: List[dict]) -> List[ChangeRecord]:
    words = timed_words(segments_raw)
    return [r.model_copy(update={"speech_clue": speech_clue_for(r, words)}) for r in records]
