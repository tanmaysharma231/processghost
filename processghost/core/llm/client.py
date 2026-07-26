"""OpenAI client wrapper with token/cost logging."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from core import config
from core.io.storage import save_json


def _load_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    return OpenAI(api_key=api_key, base_url=base_url)


class Metrics:
    def __init__(self) -> None:
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        self.records: list[dict] = []
        self.failures: list[dict] = []

    def add(self, usage: Dict[str, Any], model: str, cost_usd: float = 0.0, meta: Optional[dict] = None):
        self.tokens_in += usage.get("prompt_tokens", 0)
        self.tokens_out += usage.get("completion_tokens", 0)
        self.cost_usd += cost_usd
        record = {"timestamp": datetime.utcnow().isoformat(), "model": model, "usage": usage, "cost_usd": cost_usd}
        if meta:
            record["meta"] = meta
        self.records.append(record)

    def record_failure(self, stage: str, error: str, meta: Optional[dict] = None) -> None:
        """Record that an LLM-backed pipeline step fell back to its heuristic path.

        Never silent: every fallback used in a run is visible here and persisted with the run,
        distinct from a normal successful call.
        """
        entry = {"timestamp": datetime.utcnow().isoformat(), "stage": stage, "error": error}
        if meta:
            entry["meta"] = meta
        self.failures.append(entry)

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
        with audio_path.open("rb") as f:
            resp = self.client.audio.transcriptions.create(model=model_name, file=f, response_format="verbose_json")
        usage = resp.dict().get("usage", {})
        self.metrics.add(usage or {}, model_name)
        return resp.dict()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def chat(self, messages: list[dict], model: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        model_name = model or config.MODEL_TEXT
        resp = self.client.chat.completions.create(model=model_name, messages=messages, **kwargs)
        usage = resp.usage.dict() if hasattr(resp, "usage") else {}
        self.metrics.add(usage or {}, model_name)
        return resp.dict()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def vision(self, messages: list[dict], model: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        model_name = model or config.MODEL_VISION
        resp = self.client.chat.completions.create(model=model_name, messages=messages, **kwargs)
        usage = resp.usage.dict() if hasattr(resp, "usage") else {}
        self.metrics.add(usage or {}, model_name)
        return resp.dict()


def persist_metrics(run_id: str, metrics: Metrics, video_name: str, duration_s: float = 0.0) -> None:
    from core.config import RUN_METRICS_PATH

    RUN_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = ",".join(
        [
            datetime.utcnow().isoformat(),
            run_id,
            video_name,
            str(metrics.tokens_in),
            str(metrics.tokens_out),
            f"{metrics.cost_usd:.6f}",
            f"{duration_s:.1f}",
            config.MODEL_TEXT,
            config.MODEL_VISION,
            config.MODEL_WHISPER,
            "",
        ]
    )
    with RUN_METRICS_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def persist_run_json(run_dir: Path, payload: Dict[str, Any]) -> Path:
    return save_json(payload, run_dir / "run.json")
