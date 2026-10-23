"""Content-addressed cache for model calls: the idempotency mechanism.

The same input to the same model with the same prompt and settings returns the stored answer instead
of calling the API again, so re-running a recording costs nothing, a run that crashed late resumes
cheaply (every stage before the crash is a cache hit), and an eval can be re-scored for free.

The key covers everything that can change an answer: provider, model, system prompt, user text, the
output schema, the bytes of any attached images, and sampling settings. Change any of them and it is
a miss. Values are validated JSON; a corrupt or unreadable entry is treated as a miss, never an error.
Writes are atomic (temp file + rename), so a crash mid-write cannot leave a half-entry.

The cache is a testing aid, off by default (config.CACHE_ENABLED) and limited to the stages in
config.CACHE_STAGES -- the expensive ones, whose answer you do not need fresh. Stages whose run-to-run
variation is the thing being measured (the Writer and Reviewer) always call the model.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from langchain_core.caches import RETURN_VAL_TYPE, BaseCache
from langchain_core.load import dumpd, load

from core import config
from core.io.storage import file_sha256


def make_key(kind: str, *parts: Any, files: Iterable[Path] = ()) -> str:
    h = hashlib.sha256()
    h.update(kind.encode())
    for part in parts:
        h.update(b"\x1f")
        h.update(json.dumps(part, sort_keys=True, default=str).encode())
    for path in files:
        h.update(b"\x1e")
        h.update(file_sha256(path).encode())
    return h.hexdigest()


def _path(kind: str, key: str) -> Path:
    return config.CACHE_DIR / kind / f"{key}.json"


def get(kind: str, key: str, *, enabled: Optional[bool] = None) -> Optional[dict]:
    """`enabled` defaults to config.CACHE_ENABLED (the model-call switch). Local deterministic work
    such as screen OCR passes enabled=True: it costs nothing to reuse and is not what the switch is for."""
    if not (config.CACHE_ENABLED if enabled is None else enabled):
        return None
    try:
        return json.loads(_path(kind, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def put(kind: str, key: str, value: dict, *, enabled: Optional[bool] = None) -> None:
    if not (config.CACHE_ENABLED if enabled is None else enabled):
        return
    path = _path(kind, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, default=str), encoding="utf-8")
    tmp.replace(path)


class DiskCache(BaseCache):
    """LangChain cache over the same on-disk store, so a chat model built with cache=<DiskCache>
    needs no per-call key plumbing. LangChain's key is (prompt, llm_string): the serialized messages
    (images included) plus the model name, its settings and the bound output schema, so changing any
    of them is a miss. `hits` counts lookups that were served from disk (a hit still carries the
    original token counts, so the client uses this to price it at zero)."""

    def __init__(self) -> None:
        self.hits = 0
        self._last_key: Optional[str] = None

    def lookup(self, prompt: str, llm_string: str) -> Optional[RETURN_VAL_TYPE]:
        self._last_key = make_key("llm", llm_string, prompt)
        stored = get("llm", self._last_key)
        if stored is None:
            return None
        try:
            generations = [load(g, allowed_objects="core") for g in stored["generations"]]
        except Exception:  # noqa: BLE001 - an unreadable entry is a miss, never an error
            return None
        self.hits += 1
        return generations

    def update(self, prompt: str, llm_string: str, return_val: Sequence[Any]) -> None:
        self._last_key = make_key("llm", llm_string, prompt)
        put("llm", self._last_key, {"generations": [dumpd(g) for g in return_val]})

    def discard_last(self) -> None:
        """Drop the entry the last lookup/update touched (its answer failed validation)."""
        if self._last_key is not None:
            try:
                _path("llm", self._last_key).unlink()
            except OSError:
                pass

    def clear(self, **kwargs: Any) -> None:
        pass
