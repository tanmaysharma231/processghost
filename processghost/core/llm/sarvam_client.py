"""Sarvam AI client wrapper -- mirrors core/llm/client.py's shape (wrap the official SDK, reuse
Metrics, retry on transient failures) so core/pipeline.py can select a provider behind one factory
without the rest of the pipeline caring which one is active.

Confirmed by directly introspecting the installed `sarvamai` SDK (v0.1.28) during implementation,
NOT by trusting docs.sarvam.ai alone -- the web docs were frequently stale/404 during research, and
one summary (chat completions supporting response_format) turned out not to match the actual SDK
signature. Where something below is still unverified against a real API response (no SARVAM_API_KEY
was available while writing this), it says so explicitly in a comment. Re-verify those spots once a
real key exists and update this docstring.

Verified via SDK introspection:
- chat.completions(messages=[{"role":..., "content": "<str>"}], model="sarvam-105b") -> a pydantic
  CreateChatCompletionResponse with .choices[0].message.content and .usage -- same shape OpenAI's
  SDK produces, so core.llm.parsing.extract_chat_content() needs no changes.
- chat.completions has NO response_format parameter (contradicts one web-docs summary found during
  research) -- JSON output here is enforced by prompt instruction only, same as the rest of this
  codebase already assumes defensively (LLMResponseParseError + fallback on anything malformed).
- speech_to_text_job: create_job(model, mode, with_timestamps=True) -> job; job.upload_files([path]);
  job.start(); job.wait_until_complete(); job.get_file_results() -> {filename: [result_dict, ...]}.
  Each result dict is expected (per docs.sarvam.ai, NOT independently confirmed against a real
  response) to carry "transcript" and "timestamps": {"chunks": [...], "start_time_seconds": [...],
  "end_time_seconds": [...]} -- parallel arrays, one entry per sentence/phrase chunk.
- document_intelligence: create_job(language, output_format) -> job; job.upload_file(path) (SINGLE
  file, unlike speech_to_text_job's upload_files) -- this is why OCR here bundles all sampled frames
  into one ZIP and submits ONE job for the whole run, not one job per frame (a job-based batch flow
  has real per-job processing latency; one job per frame would be both slow and needlessly
  expensive). job.start(); job.wait_until_complete(); job.download_output(path) writes a ZIP to
  disk. The ZIP's internal structure (which file holds the page-level JSON, how pages map back to
  the original per-frame filenames) is NOT confirmed against a real response -- best-effort parsing
  below, needs verification against a real job output.
- text_to_speech.convert(text, target_language_code, speaker, model="bulbul:v3") -> a response with
  .audios: List[str] (base64-encoded WAV).
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel
from sarvamai import SarvamAI

from core import config
from core.llm import cache
from core.llm.client import Metrics
from core.llm.retry import StructuredOutputError, llm_retry


def _load_client() -> SarvamAI:
    api_key = os.getenv("SARVAM_API_KEY")
    return SarvamAI(api_subscription_key=api_key)


class SarvamClient:
    def __init__(self, metrics: Optional[Metrics] = None):
        self.client = _load_client()
        self.metrics = metrics or Metrics()

    # --- chat (intent extraction, cross-check, action-from-OCR-text) --------

    @llm_retry()
    def chat(self, messages: list[dict], model: Optional[str] = None, stage: str = "unknown", **kwargs) -> Dict[str, Any]:
        model_name = model or config.SARVAM_MODEL_CHAT
        # No response_format param on this SDK's chat.completions -- see module docstring. Any
        # kwargs beyond what the SDK's typed signature accepts (e.g. a leftover response_format
        # from a caller written against the OpenAI-shaped interface) are dropped rather than
        # raising, so both providers can share the same call sites in core/pipeline.py.
        kwargs.pop("response_format", None)
        t0 = time.monotonic()
        resp = self.client.chat.completions(messages=messages, model=model_name, **kwargs)
        latency_ms = (time.monotonic() - t0) * 1000
        data = resp.model_dump()
        usage = data.get("usage") or {}
        self.metrics.add(usage, model_name, stage=stage, latency_ms=latency_ms)
        return data

    # --- structured chat (Writer/Reviewer/Checker, Phase 3) -----------------

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
        """Same interface as LLMClient.structured_chat (core/llm/client.py) so core/pipeline.py
        never branches on provider at the call site -- but the mechanism is unchanged from before
        Phase 3: no true vision model exists on Sarvam (see module docstring), so `images` is
        ignored here; the Checker's Sarvam path OCRs frames separately and folds the extracted text
        into `user_text` before calling this. JSON output is still enforced by prompt instruction
        only (no response_format on this SDK), so this ends with an explicit
        schema.model_validate(json.loads(...)) rather than relying on API-level strict-schema
        enforcement the way the OpenAI path does. `stage` passes through to the underlying `chat`
        call's Metrics.add, same tracing purpose as the OpenAI path.
        """
        from core.llm.parsing import extract_chat_content  # local import: avoid a cycle at module load

        model_name = model or config.SARVAM_MODEL_CHAT
        key = cache.make_key("structured", "sarvam", model_name, system_prompt, user_text, schema.__name__, schema.model_json_schema())
        hit = cache.get("structured", key)
        if hit is not None:
            parsed = schema.model_validate(hit)
            self.metrics.add({}, model_name, stage=stage, latency_ms=0.0, meta={"cached": True}, cost_usd_override=0.0)
            self.metrics.record_payload(stage, {**(payload_extra or {}), **parsed.model_dump(mode="json")})
            return parsed

        response = self.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            model=model,
            stage=stage,
        )
        content = extract_chat_content(response)
        try:
            parsed = schema.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValueError) as exc:
            raise StructuredOutputError(f"structured output did not match {schema.__name__}: {exc}") from exc
        # Same trace-payload mechanism as LLMClient.structured_chat (core/llm/client.py) -- a
        # separate trace line, not folded into self.chat's own metrics.add call above, so
        # cost/token accounting there is never at risk of being duplicated.
        payload = parsed.model_dump(mode="json")
        cache.put("structured", key, payload)
        if payload_extra:
            payload = {**payload_extra, **payload}
        self.metrics.record_payload(stage, payload)
        return parsed

    # --- transcription (batch job, gives chunk-level timestamps) -----------

    def transcribe(self, audio_path: Path) -> Dict[str, Any]:
        """Submit, upload, run, and wait on a speech-to-text batch job; return the first file's
        result dict. Raises on any failure -- callers degrade the same way the OpenAI path does."""
        t0 = time.monotonic()
        job = self.client.speech_to_text_job.create_job(
            model=config.SARVAM_MODEL_STT,
            mode="transcribe",
            with_timestamps=True,
        )
        job.upload_files([str(audio_path)])
        job.start()
        job.wait_until_complete(poll_interval=5, timeout=600)
        latency_ms = (time.monotonic() - t0) * 1000
        results = job.get_file_results()
        if not results:
            raise RuntimeError("Sarvam STT job completed but returned no file results")
        first_file_results = next(iter(results.values()))
        if not first_file_results:
            raise RuntimeError("Sarvam STT job's result list for the uploaded file was empty")
        result = first_file_results[0]
        usage = result.get("usage") or {}
        # No confirmed per-minute Sarvam STT price (see module docstring -- unverified against a
        # real response); estimate_cost_usd correctly marks "saaras:v3" pricing "unknown" rather
        # than guessing a number.
        self.metrics.add(
            usage,
            config.SARVAM_MODEL_STT,
            stage="transcribe",
            latency_ms=latency_ms,
            meta={"job_id": getattr(job, "job_id", None)},
        )
        return result

    # --- OCR (one batched job over all sampled frames, not one job/frame) --

    def ocr_extract_frames(self, frame_paths: List[Path], workdir: Path) -> Dict[str, str]:
        """Bundle frame_paths into a single ZIP, run ONE document-intelligence job over all of
        them, and return {frame filename: extracted text/markdown for that page}. Raises on any
        failure -- callers treat the whole batch as failed and fall back per-frame, same
        whole-batch-degrades philosophy as _extract_intents_from_transcript.

        workdir is used for the intermediate ZIP (input) and the downloaded output ZIP -- pass the
        run's own directory so these are cleaned up / inspectable alongside the rest of the run's
        artifacts, not left in a system temp dir.
        """
        if not frame_paths:
            return {}

        input_zip = workdir / "ocr_input_frames.zip"
        with zipfile.ZipFile(input_zip, "w") as zf:
            for fp in frame_paths:
                zf.write(fp, arcname=fp.name)

        t0 = time.monotonic()
        job = self.client.document_intelligence.create_job(
            language=config.SARVAM_DOC_INTELLIGENCE_LANGUAGE,
            output_format="md",
        )
        job.upload_file(str(input_zip))
        job.start()
        job.wait_until_complete(poll_interval=5, timeout=600)

        output_zip_path = workdir / "ocr_output.zip"
        job.download_output(str(output_zip_path))
        latency_ms = (time.monotonic() - t0) * 1000

        # Not token-priced -- Sarvam's document-intelligence job is billed per page (see
        # core/config.py: SARVAM_OCR_COST_PER_PAGE). This is the one call this project's own
        # metrics never recorded at all before this change.
        self.metrics.add(
            {},
            "sarvam-document-intelligence",
            stage="checker_ocr_batch",
            latency_ms=latency_ms,
            meta={"page_count": len(frame_paths), "job_id": getattr(job, "job_id", None)},
            cost_usd_override=config.SARVAM_OCR_COST_PER_PAGE * len(frame_paths),
        )

        return _parse_ocr_output_zip(output_zip_path, {fp.name for fp in frame_paths})

    # --- text-to-speech (Bulbul) --------------------------------------------

    @llm_retry()
    def text_to_speech(
        self,
        text: str,
        speaker: Optional[str] = None,
        target_language_code: Optional[str] = None,
        model: Optional[str] = None,
    ) -> bytes:
        model_name = model or config.SARVAM_MODEL_TTS
        t0 = time.monotonic()
        resp = self.client.text_to_speech.convert(
            text=text,
            target_language_code=target_language_code or config.SARVAM_TTS_LANGUAGE_CODE,
            speaker=speaker or config.SARVAM_TTS_SPEAKER,
            model=model_name,
        )
        latency_ms = (time.monotonic() - t0) * 1000
        self.metrics.add({}, model_name, stage="tts", latency_ms=latency_ms, meta={"chars": len(text)})
        if not resp.audios:
            raise RuntimeError("Sarvam TTS response contained no audio")
        return base64.b64decode(resp.audios[0])


def _parse_ocr_output_zip(output_zip_path: Path, expected_filenames: set[str]) -> Dict[str, str]:
    """Best-effort parse of a document-intelligence job's downloaded output ZIP into
    {original frame filename: extracted text}. UNVERIFIED against a real response -- the exact
    internal structure (which file holds page-level JSON, how pages map back to input filenames)
    was not confirmed during implementation (no SARVAM_API_KEY available). Looks for a .json file
    and tries a couple of reasonable shapes; raises RuntimeError with the file listing if none
    match, so a mismatch is loud and fixable rather than silently wrong.
    """
    with zipfile.ZipFile(output_zip_path) as zf:
        names = zf.namelist()
        json_names = [n for n in names if n.lower().endswith(".json")]
        if not json_names:
            raise RuntimeError(f"No JSON file found in Sarvam OCR output ZIP; contents: {names}")

        with zf.open(json_names[0]) as f:
            data = json.load(io.TextIOWrapper(f, encoding="utf-8"))

    pages = data.get("pages") if isinstance(data, dict) else data
    if not isinstance(pages, list):
        raise RuntimeError(f"Unexpected Sarvam OCR JSON shape (expected a 'pages' list): {type(data)}")

    result: Dict[str, str] = {}
    sorted_filenames = sorted(expected_filenames)
    for i, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        text = page.get("markdown") or page.get("text") or page.get("content") or ""
        # Prefer an explicit filename reference on the page if present; otherwise fall back to
        # positional mapping against the sorted input filenames (the order frames were zipped in).
        filename = page.get("source_file") or page.get("filename")
        if not filename and i < len(sorted_filenames):
            filename = sorted_filenames[i]
        if filename:
            result[filename] = text

    return result
