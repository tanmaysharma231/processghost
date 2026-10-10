"""OpenAI client wrapper with token/cost logging.

v2 note: `structured_chat` (added in Phase 3) is the LangChain-based path used by the Writer,
Reviewer, and Checker stages -- see core/llm/parsing.py: build_sop_from_writer_output /
merge_reviewer_output and processghost-v2-writer-reviewer-checker-plan.md. `chat`/`vision` below
are the original raw-SDK methods, left untouched: they're still called by the dead v1 pipeline
functions in core/pipeline.py (kept alive, unimported by run_pipeline, until Phase 3 acceptance
per the owner's plan) and by tests/test_pipeline_fallback.py's coverage of them. `transcribe` also
stays on the raw SDK -- Whisper is not a chat completion, so LangChain has nothing to add there.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import OpenAI
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from core import config
from core.io.images import frame_to_data_uri
from core.io.storage import save_json


def _load_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    return OpenAI(api_key=api_key, base_url=base_url)


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
        (Whisper: per-minute of audio; Sarvam OCR: per-page) -- the caller computes it from
        config.WHISPER_COST_PER_MINUTE / config.SARVAM_OCR_COST_PER_PAGE and passes it directly,
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
        duplicated by a caller that (re)records a payload. Both structured_chat implementations
        (core/llm/client.py: LLMClient, core/llm/sarvam_client.py: SarvamClient) call this right
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

    def _write_trace_line(self, event: dict) -> None:
        if self.trace_path is None:
            return
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
    def __init__(self, metrics: Optional[Metrics] = None):
        self.client = _load_client()
        self.metrics = metrics or Metrics()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def transcribe(self, audio_path: Path, model: str | None = None) -> Dict[str, Any]:
        model_name = model or config.MODEL_WHISPER
        t0 = time.monotonic()
        with audio_path.open("rb") as f:
            resp = self.client.audio.transcriptions.create(model=model_name, file=f, response_format="verbose_json")
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
        return resp_dict

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def chat(self, messages: list[dict], model: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        model_name = model or config.MODEL_TEXT
        t0 = time.monotonic()
        resp = self.client.chat.completions.create(model=model_name, messages=messages, **kwargs)
        latency_ms = (time.monotonic() - t0) * 1000
        usage = resp.usage.dict() if hasattr(resp, "usage") else {}
        self.metrics.add(usage or {}, model_name, stage="legacy_chat", latency_ms=latency_ms)
        return resp.dict()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def vision(self, messages: list[dict], model: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        model_name = model or config.MODEL_VISION
        t0 = time.monotonic()
        resp = self.client.chat.completions.create(model=model_name, messages=messages, **kwargs)
        latency_ms = (time.monotonic() - t0) * 1000
        usage = resp.usage.dict() if hasattr(resp, "usage") else {}
        self.metrics.add(usage or {}, model_name, stage="legacy_vision", latency_ms=latency_ms)
        return resp.dict()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
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
        """LangChain path: ask the model for `schema`-shaped output directly via
        with_structured_output(method="json_schema") -- no hand-rolled JSON parsing at the call
        site (core/llm/parsing.py's build_sop_from_writer_output / merge_reviewer_output still
        apply business rules like segment/transition coverage on top of the validated object, but
        shape validation itself is pydantic's job now, for both providers).

        Images (Checker's need) are attached as base64 ImageContentBlocks per the installed
        langchain-core content-block API -- checked directly against the installed package rather
        than older image_url-style examples, which are a different (pre-v1) LangChain convention.
        Reuses frame_to_data_uri's resize step (core.config.VISION_MAX_DIMENSION) and strips the
        data-URI prefix rather than duplicating the encode/resize logic.

        include_raw=True is required to get token usage back for Metrics -- with_structured_output
        alone returns only the parsed object. Raises RuntimeError if the model's output didn't
        parse against `schema` even after LangChain's own retries.

        `stage` ("writer" / "reviewer" / "checker" / ...) is passed straight through to
        Metrics.add so every call is traceable back to which pipeline stage made it -- see
        core/pipeline.py's call sites. Also passed to Metrics.record_payload alongside the parsed
        result itself, so the trace carries the real output, not just cost/latency metadata.

        `payload_extra` merges into that recorded payload without touching `schema`'s own shape --
        for the Checker, the caller has a flag_id CheckerOutput itself doesn't carry (it's a
        per-flag call; the flag is known by the caller, not part of what the model was asked to
        return), so `_check_one_flag` (core/pipeline.py) passes `{"flag_id": flag.flag_id}` here
        purely so a live trace consumer can tell which flag a given payload resolved.
        """
        model_name = model or (config.MODEL_VISION if images else config.MODEL_TEXT)
        content: list = [{"type": "text", "text": user_text}]
        for frame_path in images or []:
            data_uri = frame_to_data_uri(frame_path, max_dimension=config.VISION_MAX_DIMENSION)
            _, _, b64 = data_uri.partition(",")
            content.append({"type": "image", "base64": b64, "mime_type": "image/png"})

        messages = [SystemMessage(content=system_prompt), HumanMessage(content=content)]
        chat_model = ChatOpenAI(
            model=model_name, api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("OPENAI_API_BASE") or None
        ).with_structured_output(schema, method="json_schema", include_raw=True)

        t0 = time.monotonic()
        result = chat_model.invoke(messages)
        latency_ms = (time.monotonic() - t0) * 1000
        if result.get("parsing_error") is not None:
            raise RuntimeError(f"structured output did not match {schema.__name__}: {result['parsing_error']}")

        raw = result["raw"]
        usage_metadata = getattr(raw, "usage_metadata", None) or {}
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
        if payload_extra:
            payload = {**payload_extra, **payload}
        self.metrics.record_payload(stage, payload)
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
