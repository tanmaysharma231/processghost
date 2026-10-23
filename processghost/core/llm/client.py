"""One LLM client for every provider, built on LangChain, plus the run's token/cost/trace accounting.

`LLMClient.structured_chat` is the single call path for the Writer, Reviewer, Checker, Reconciler,
data Checker and read fallback. The provider (config.LLM_PROVIDER: "openai" or "anthropic") only
changes which chat model `init_chat_model` builds; schema validation, images, retry, caching and
metrics are the same code for both. See core/llm/parsing.py: build_sop_from_writer_output /
merge_reviewer_output for the business rules applied on top of the validated object.

Transcription (`transcribe`) stays on the raw OpenAI SDK -- Whisper is not a chat completion and
Anthropic has no speech-to-text -- so the OpenAI client is built only when a transcription is
actually requested.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from openai import OpenAI
from pydantic import BaseModel

from core import config
from core.llm import cache
from core.llm.retry import StructuredOutputError, llm_retry
from core.io.images import frame_to_image_block
from core.io.storage import save_json


def _load_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    return OpenAI(api_key=api_key, base_url=base_url, timeout=config.LLM_TIMEOUT_S)


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> Tuple[float, bool]:
    """Look up `model` in config.PRICING_PER_1M_TOKENS and estimate its cost for this call.

    Returns (cost_usd, known) -- known=False for a model not in the table, in which case cost_usd
    is 0.0. Callers must record that unknown-ness in the metrics entry's meta (see Metrics.add)
    rather than let a 0.0 cost look indistinguishable from a genuinely free/untracked call.
    """
    pricing = config.PRICING_PER_1M_TOKENS.get(model)
    if pricing is None:
        return 0.0, False
    prompt_rate, completion_rate = pricing
    cost = (prompt_tokens / 1_000_000) * prompt_rate + (completion_tokens / 1_000_000) * completion_rate
    return cost, True


class Metrics:
    def __init__(self, trace_path: Optional[Path] = None) -> None:
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        self.records: list[dict] = []
        self.failures: list[dict] = []
        # Set once the run's directory exists (core/pipeline.py: run_pipeline, right after
        # storage.new_run_dir()) so every call from then on appends one line to
        # runs/<run_id>/trace.jsonl as it happens -- crash-safe, and loadable by any later tracing
        # tool without parsing run.json's nested structure. None until then; add()/record_failure()
        # simply skip the file write in that case.
        self.trace_path: Optional[Path] = trace_path
        # Stamped on every trace line (set by run_pipeline) so lines from different runs can be
        # concatenated and still told apart.
        self.run_id: Optional[str] = None

    def over_budget(self) -> bool:
        """True once estimated spend has passed config.MAX_RUN_COST_USD (0 disables the limit)."""
        return config.MAX_RUN_COST_USD > 0 and self.cost_usd >= config.MAX_RUN_COST_USD

    def add(
        self,
        usage: Dict[str, Any],
        model: str,
        stage: str,
        *,
        latency_ms: Optional[float] = None,
        meta: Optional[dict] = None,
        cost_usd_override: Optional[float] = None,
    ) -> None:
        """cost_usd_override is for calls priced by something other than prompt/completion tokens
        (Whisper: per-minute of audio) -- the caller computes it from
        config.WHISPER_COST_PER_MINUTE and passes it directly,
        since estimate_cost_usd's token-rate table has no meaningful answer for those. Token-based
        calls (the large majority -- every Writer/Reviewer/Checker call) leave this None and get
        cost computed automatically, which is the whole point: it can't be forgotten if nothing
        has to remember to pass it."""
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        if cost_usd_override is not None:
            cost_usd, known_pricing = cost_usd_override, True
        else:
            cost_usd, known_pricing = estimate_cost_usd(model, prompt_tokens, completion_tokens)

        self.tokens_in += prompt_tokens
        self.tokens_out += completion_tokens
        self.cost_usd += cost_usd

        record_meta = dict(meta or {})
        if not known_pricing:
            record_meta["pricing"] = "unknown"

        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "event": "call",
            "stage": stage,
            "model": model,
            "usage": usage,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
        }
        if record_meta:
            record["meta"] = record_meta
        self.records.append(record)
        self._write_trace_line(record)

    def record_payload(self, stage: str, payload: dict) -> None:
        """Write the actual structured output of a Writer/Reviewer/Checker call to the trace, as
        its own line -- separate from add()'s "call" event so cost/token accounting can never be
        duplicated by a caller that (re)records a payload. LLMClient.structured_chat calls this right
        after the model's output validates against its schema, passing `payload.model_dump()`.

        This is what turns trace.jsonl from "a Checker call finished, 9s, $0.05" into "here is
        what it actually resolved" -- the one thing missing for a live progress view to show real
        content instead of just call metadata. See docs/18-known-gaps-v2.md.
        """
        entry = {"timestamp": datetime.utcnow().isoformat(), "event": "payload", "stage": stage, "payload": payload}
        self._write_trace_line(entry)

    def record_failure(self, stage: str, error: str, meta: Optional[dict] = None) -> None:
        """Record that an LLM-backed pipeline step fell back to its heuristic path (or, for the
        Writer/Reviewer/Checker, that the stage itself degraded/aborted).

        Never silent: every fallback used in a run is visible here and persisted with the run,
        distinct from a normal successful call.
        """
        entry = {"timestamp": datetime.utcnow().isoformat(), "event": "failure", "stage": stage, "error": error}
        if meta:
            entry["meta"] = meta
        self.failures.append(entry)
        self._write_trace_line(entry)

    @contextmanager
    def stage(self, name: str):
        """Bracket a pipeline stage with stage_start / stage_end trace events (the end event carries
        duration and whether the stage raised), so where a run's time went and where it stopped are
        readable from trace.jsonl alone."""
        t0 = time.monotonic()
        self._write_trace_line({"timestamp": datetime.utcnow().isoformat(), "event": "stage_start", "stage": name})
        ok = False
        try:
            yield
            ok = True
        finally:
            self._write_trace_line(
                {
                    "timestamp": datetime.utcnow().isoformat(),
                    "event": "stage_end",
                    "stage": name,
                    "duration_s": round(time.monotonic() - t0, 3),
                    "ok": ok,
                }
            )

    def _write_trace_line(self, event: dict) -> None:
        if self.trace_path is None:
            return
        if self.run_id is not None:
            event = {"run_id": self.run_id, **event}
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")

    def to_dict(self) -> dict:
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd_estimate": round(self.cost_usd, 6),
            "calls": self.records,
            "fallbacks": self.failures,
        }


class LLMClient:
    def __init__(self, metrics: Optional[Metrics] = None, provider: Optional[str] = None):
        self.provider = provider or config.LLM_PROVIDER
        self.metrics = metrics or Metrics()
        self._openai: Optional[OpenAI] = None
        self._disk_cache = cache.DiskCache()

    @property
    def client(self) -> OpenAI:  # used only by transcribe()
        if self._openai is None:
            self._openai = _load_client()
        return self._openai

    def _default_model(self, with_images: bool) -> str:
        if self.provider == "anthropic":
            return config.ANTHROPIC_MODEL_VISION if with_images else config.ANTHROPIC_MODEL_TEXT
        return config.MODEL_VISION if with_images else config.MODEL_TEXT

    def _chat_model(self, model_name: str, stage: str):
        """The one place a chat model is built. max_retries=0: core/llm/retry.py owns retrying, so one
        call is never retried by two layers. Anthropic gets no temperature (the newest Claude models
        reject non-default sampling values); the disk cache covers repeatability there."""
        kwargs: Dict[str, Any] = {"max_retries": 0, "timeout": config.LLM_TIMEOUT_S}
        if self.provider == "anthropic":
            kwargs["max_tokens"] = config.ANTHROPIC_MAX_TOKENS
        else:
            kwargs["temperature"] = config.LLM_TEMPERATURE
        use_cache = config.CACHE_ENABLED and stage in config.CACHE_STAGES
        kwargs["cache"] = self._disk_cache if use_cache else False
        return init_chat_model(model_name, model_provider=self.provider, **kwargs)

    @llm_retry()
    def transcribe(self, audio_path: Path, model: str | None = None) -> Dict[str, Any]:
        model_name = model or config.MODEL_WHISPER
        use_cache = config.CACHE_ENABLED and "transcribe" in config.CACHE_STAGES
        key = cache.make_key("transcribe", "openai", model_name, "words", files=[audio_path])
        hit = cache.get("transcribe", key) if use_cache else None
        if hit is not None:
            self.metrics.add({}, model_name, stage="transcribe", latency_ms=0.0, meta={"cached": True}, cost_usd_override=0.0)
            return hit
        t0 = time.monotonic()
        with audio_path.open("rb") as f:
            resp = self.client.audio.transcriptions.create(
                model=model_name, file=f, response_format="verbose_json", timestamp_granularities=["word", "segment"]
            )
        latency_ms = (time.monotonic() - t0) * 1000
        resp_dict = resp.dict()
        usage = resp_dict.get("usage", {})
        # Whisper is priced per minute of audio, not per token -- resp_dict["duration"] (seconds,
        # verbose_json) is what's actually billed, not anything in `usage`.
        duration_s = resp_dict.get("duration") or 0.0
        cost_usd = (duration_s / 60.0) * config.WHISPER_COST_PER_MINUTE
        self.metrics.add(
            usage or {},
            model_name,
            stage="transcribe",
            latency_ms=latency_ms,
            meta={"duration_s": duration_s},
            cost_usd_override=cost_usd,
        )
        if use_cache:
            cache.put("transcribe", key, resp_dict)
        return resp_dict

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
        """Ask the model for `schema`-shaped output via with_structured_output(method="json_schema")
        -- no hand-rolled JSON parsing at the call site. Business rules (segment/transition
        coverage) are applied afterwards by core/llm/parsing.py on the validated object.

        Images are attached as provider-neutral base64 image blocks (core.io.images:
        frame_to_image_block, downscaled to config.VISION_MAX_DIMENSION). include_raw=True is what
        returns token usage for Metrics. A response that does not match `schema` raises
        StructuredOutputError, which llm_retry re-asks once.

        `stage` ("writer" / "reviewer" / "checker" / ...) tags the metrics and trace line, and decides
        whether the disk cache applies (config.CACHE_STAGES). `payload_extra` merges into the payload
        recorded to the trace without touching `schema`'s own shape -- e.g. the Checker's flag_id.
        """
        model_name = model or self._default_model(bool(images))
        content: list = [{"type": "text", "text": user_text}]
        content += [frame_to_image_block(p, max_dimension=config.VISION_MAX_DIMENSION) for p in images or []]
        chat_model = self._chat_model(model_name, stage).with_structured_output(schema, method="json_schema", include_raw=True)

        hits_before = self._disk_cache.hits
        t0 = time.monotonic()
        result = chat_model.invoke([SystemMessage(content=system_prompt), HumanMessage(content=content)])
        latency_ms = (time.monotonic() - t0) * 1000
        if result.get("parsing_error") is not None:
            self._disk_cache.discard_last()  # never keep a bad answer, or the re-ask would read it back
            raise StructuredOutputError(f"structured output did not match {schema.__name__}: {result['parsing_error']}")

        parsed = result["parsed"]
        if self._disk_cache.hits > hits_before:
            self.metrics.add({}, model_name, stage=stage, latency_ms=0.0, meta={"cached": True}, cost_usd_override=0.0)
        else:
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
        self.metrics.record_payload(stage, {**(payload_extra or {}), **parsed.model_dump(mode="json")})
        return parsed


def persist_metrics(run_id: str, metrics: Metrics, video_name: str, duration_s: float = 0.0) -> None:
    """Append one summary row for this run. Reports what actually happened -- the distinct models
    this run really called (semicolon-joined; a comma would corrupt this naive, unquoted CSV) and
    how many calls were made -- rather than the three fixed config model constants every prior row
    used regardless of which models were actually invoked or how many times (the Checker alone can
    make anywhere from 0 to MAX_CHECKED_FLAGS calls). Per-call detail lives in
    runs/<run_id>/trace.jsonl; this file stays a one-line-per-run summary."""
    from core.config import RUN_METRICS_PATH

    RUN_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    models_used = ";".join(sorted({r["model"] for r in metrics.records})) or "none"
    line = ",".join(
        [
            datetime.utcnow().isoformat(),
            run_id,
            video_name,
            str(metrics.tokens_in),
            str(metrics.tokens_out),
            f"{metrics.cost_usd:.6f}",
            f"{duration_s:.1f}",
            models_used,
            str(len(metrics.records)),
            str(len(metrics.failures)),
        ]
    )
    with RUN_METRICS_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def persist_run_json(run_dir: Path, payload: Dict[str, Any]) -> Path:
    return save_json(payload, run_dir / "run.json")
