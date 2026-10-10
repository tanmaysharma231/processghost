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
