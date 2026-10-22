"""Anthropic (Claude) provider: same `structured_chat` contract as LLMClient, reasoning on Claude.

Differences that matter, all from the Claude API rules for the current models:
- No temperature: non-default sampling values are rejected, so none is sent. Reruns are made repeatable
  by the content-addressed cache, not by temperature 0.
- Structured output uses `method="json_schema"` (Claude's native structured outputs). The LangChain
  default, forced tool calling, returns a 400 on the newest models.
- Transcription stays on OpenAI Whisper (Anthropic has no speech-to-text). The OpenAI client is built
  only when a transcription is actually requested, so a cached transcript needs no OpenAI key.

UNVERIFIED against the live API: the installed langchain-anthropic sends structured output through the
older `output_format` parameter, and no Anthropic key was available to try it. Run
`python -m scripts.smoke_anthropic` first; if it fails, upgrade langchain-anthropic and anthropic
(and re-run the test suite) before relying on this provider.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional, Type

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from core import config
from core.io.images import frame_to_data_uri
from core.llm import cache
from core.llm.client import LLMClient, Metrics, _load_client
from core.llm.retry import StructuredOutputError, llm_retry


class AnthropicClient(LLMClient):
    def __init__(self, metrics: Optional[Metrics] = None):
        # Deliberately not calling LLMClient.__init__: that builds an OpenAI client and needs OPENAI_API_KEY.
        self.metrics = metrics or Metrics()
        self._openai = None

    @property
    def client(self):  # used only by LLMClient.transcribe
        if self._openai is None:
            self._openai = _load_client()
        return self._openai

    @llm_retry()
    def structured_chat(
        self,
        system_prompt: str,
        user_text: str,
        schema: Type[BaseModel],
        *,
        stage: str = "unknown",
        images: Optional[List[Path]] = None,
        model: Optional[str] = None,
        payload_extra: Optional[dict] = None,
    ) -> BaseModel:
        model_name = model or (config.ANTHROPIC_MODEL_VISION if images else config.ANTHROPIC_MODEL_TEXT)
        content: list = [{"type": "text", "text": user_text}]
        for frame_path in images or []:
            _, _, b64 = frame_to_data_uri(frame_path, max_dimension=config.VISION_MAX_DIMENSION).partition(",")
            content.append({"type": "image", "base64": b64, "mime_type": "image/png"})

        key = cache.make_key(
            "structured", "anthropic", model_name, system_prompt, user_text, schema.__name__,
            schema.model_json_schema(), files=images or [],
        )
        hit = cache.get("structured", key)
        if hit is not None:
            parsed = schema.model_validate(hit)
            self.metrics.add({}, model_name, stage=stage, latency_ms=0.0, meta={"cached": True}, cost_usd_override=0.0)
            self.metrics.record_payload(stage, {**(payload_extra or {}), **parsed.model_dump(mode="json")})
            return parsed

        # max_retries=0: core/llm/retry.py owns retrying, so one call is never retried by two layers.
        chat_model = ChatAnthropic(
            model=model_name, api_key=os.getenv("ANTHROPIC_API_KEY"), max_tokens=config.ANTHROPIC_MAX_TOKENS,
            max_retries=0, default_request_timeout=config.LLM_TIMEOUT_S,
        ).with_structured_output(schema, method="json_schema", include_raw=True)

        t0 = time.monotonic()
        result = chat_model.invoke([SystemMessage(content=system_prompt), HumanMessage(content=content)])
        latency_ms = (time.monotonic() - t0) * 1000
        if result.get("parsing_error") is not None:
            raise StructuredOutputError(f"structured output did not match {schema.__name__}: {result['parsing_error']}")

        usage_metadata = getattr(result["raw"], "usage_metadata", None) or {}
        self.metrics.add(
            {
                "prompt_tokens": usage_metadata.get("input_tokens", 0),
                "completion_tokens": usage_metadata.get("output_tokens", 0),
            },
            model_name,
            stage=stage,
            latency_ms=latency_ms,
            meta={"images": len(images)} if images else None,
        )
        parsed = result["parsed"]
        payload = parsed.model_dump(mode="json")
        cache.put("structured", key, payload)
        if payload_extra:
            payload = {**payload_extra, **payload}
        self.metrics.record_payload(stage, payload)
        return parsed
