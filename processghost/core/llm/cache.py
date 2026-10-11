"""Content-addressed cache for model calls: the idempotency mechanism.

The same input to the same model with the same prompt and settings returns the stored answer instead
of calling the API again, so re-running a recording costs nothing, a run that crashed late resumes
cheaply (every stage before the crash is a cache hit), and an eval can be re-scored for free.

The key covers everything that can change an answer: provider, model, system prompt, user text, the
output schema, the bytes of any attached images, and sampling settings. Change any of them and it is
a miss. Values are validated JSON; a corrupt or unreadable entry is treated as a miss, never an error.
Writes are atomic (temp file + rename), so a crash mid-write cannot leave a half-entry.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Optional

from core import config


def _file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_key(kind: str, *parts: Any, files: Iterable[Path] = ()) -> str:
    h = hashlib.sha256()
    h.update(kind.encode())
    for part in parts:
        h.update(b"\x1f")
        h.update(json.dumps(part, sort_keys=True, default=str).encode())
    for path in files:
        h.update(b"\x1e")
        h.update(_file_digest(path).encode())
    return h.hexdigest()


def _path(kind: str, key: str) -> Path:
    return config.CACHE_DIR / kind / f"{key}.json"


def get(kind: str, key: str) -> Optional[dict]:
    if not config.CACHE_ENABLED:
        return None
    try:
        return json.loads(_path(kind, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def put(kind: str, key: str, value: dict) -> None:
    if not config.CACHE_ENABLED:
        return
    path = _path(kind, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, default=str), encoding="utf-8")
    tmp.replace(path)
