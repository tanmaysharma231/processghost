"""Shared substring-grounding primitive.

Originally lived only inside core/llm/parsing.py as the deterministic check behind the Reviewer's
<transitions> quote requirement -- a model that genuinely cannot bridge a step pair cannot produce
a real quote, so an invented, paraphrased, or missing quote fails here rather than being trusted
(see docs/18-known-gaps-v2.md entry 1). Promoted to its own module because the intake stage needs
the identical check to ground a document-derived fact against the passage it claims to come from,
and core.llm.parsing already imports the sop models -- keeping the primitive here avoids a cycle.
core.llm.parsing keeps _quote_is_grounded / _normalize_for_quote_match as private aliases so every
existing call site and test reference stays valid unchanged.
"""
from __future__ import annotations

import re
from typing import Optional

_WHITESPACE_RE = re.compile(r"\s+")

_STOPWORDS = {
    "this", "that", "these", "those", "with", "from", "into", "onto", "over", "under",
    "then", "than", "when", "what", "where", "which", "while", "will", "would", "could",
    "should", "there", "here", "have", "been", "were", "does", "each", "some", "such",
}


def normalize_for_quote_match(text: str) -> str:
    """Lowercase and collapse whitespace so a quote match tolerates the kind of harmless
    reformatting a model does when copying text (extra spaces, a trailing period the model
    dropped) without tolerating an actually different sentence."""
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def compact(text: str) -> str:
    """Lowercase and drop every whitespace and pipe character. OCR routinely drops or adds spaces
    (`AMZN WEB SVCS` -> `AMZNWEBSVCS`) and the screen records join fragments with ` | `, so grounding a
    quote against OCR text compares the text with those removed."""
    return re.sub(r"[\s|]+", "", text or "").lower()


def quote_in_ocr(quote: str, ocr_text: str, min_chars: int = 3) -> bool:
    """True if `quote`, with whitespace and pipes removed, is a substring of `ocr_text` likewise
    compacted. Quotes shorter than `min_chars` never ground (a 1-2 character quote matches anything)."""
    q = compact(quote)
    return len(q) >= min_chars and q in compact(ocr_text)


def quote_is_grounded(quote: str, haystack_text: str) -> bool:
    """True only if `quote` (normalized) is an actual substring of `haystack_text` (normalized).
    The haystack is a transcript for Reviewer transitions, or a source passage's text for an
    intake-derived fact -- same mechanism, different haystack."""
    quote = (quote or "").strip()
    if not quote:
        return False
    return normalize_for_quote_match(quote) in normalize_for_quote_match(haystack_text)


def shares_distinctive_token(a: str, b: str) -> bool:
    """True if `a` and `b` share at least one alphanumeric token of length >= 4 that is not a
    common stopword. A lexical proxy for "these two statements are probably about the same thing",
    not semantic agreement -- used only where a false negative is safe (something stays unaddressed
    rather than being wrongly confirmed) and a false positive still requires a real shared word."""
    tokens_a = {t for t in re.split(r"[^a-z0-9]+", a.lower()) if len(t) >= 4 and t not in _STOPWORDS}
    tokens_b = {t for t in re.split(r"[^a-z0-9]+", b.lower()) if len(t) >= 4 and t not in _STOPWORDS}
    return bool(tokens_a & tokens_b)


_WORD_RE = re.compile(r"[a-z0-9]+")
_ECHO_STOP = _STOPWORDS | {"step", "shows", "visible", "screen", "sheet", "image", "tile"}


def content_words(text: str) -> set:
    return {w for w in _WORD_RE.findall(normalize_for_quote_match(text)) if len(w) >= 4 and w not in _ECHO_STOP}


def echo_score(observed: str, prompt_text: str) -> float | None:
    """Share of `observed`'s content words that already appear in `prompt_text`. A high score means
    an "observation" was lifted from the prompt (step text, transcript, question) instead of read off
    the evidence -- see docs/18-known-gaps-v2.md entry 5. None when `observed` has no content words.
    A lexical proxy: it cannot prove an answer wrong, only that it adds nothing the model was not
    already told."""
    words = content_words(observed)
    if not words:
        return None
    return len(words & content_words(prompt_text)) / len(words)


_THOUSANDS = re.compile(r"(?<=\d)[,.](?=\d{3}\b)")


def norm_value(value: object) -> str:
    """Whitespace-, pipe- and case-insensitive text with thousands separators removed, so OCR spacing
    jitter and '45,000' vs '45000' are the same value."""
    return _THOUSANDS.sub("", compact(str(value)))


def to_number(value: object) -> Optional[float]:
    """A displayed number ('45,000') as a float, or None when it is not one."""
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None
