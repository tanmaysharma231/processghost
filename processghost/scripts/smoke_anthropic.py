"""One tiny real call to check the Anthropic provider works end to end (structured output, usage, cost).
Costs well under one cent. Needs ANTHROPIC_API_KEY.

Usage (from the processghost/ directory):
    python -m scripts.smoke_anthropic
"""
from __future__ import annotations

import os

from pydantic import BaseModel

from core import config
from core.llm.client import LLMClient, Metrics


class Smoke(BaseModel):
    answer: int
    unit: str


def main() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    config.CACHE_ENABLED = False
    metrics = Metrics()
    out = LLMClient(metrics=metrics, provider="anthropic").structured_chat(
        "Answer with the requested structure only.", "How many minutes are in two hours?", Smoke, stage="smoke"
    )
    print("model:", config.ANTHROPIC_MODEL_TEXT)
    print("parsed:", out)
    print("tokens in/out: %d/%d | cost: $%.5f" % (metrics.tokens_in, metrics.tokens_out, metrics.cost_usd))


if __name__ == "__main__":
    main()
