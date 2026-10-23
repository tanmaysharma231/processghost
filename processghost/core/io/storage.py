"""Run artifact storage helpers."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from core.config import RUNS_DIR


def new_run_dir() -> Path:
    run_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    path = RUNS_DIR / run_id
    (path / "frames").mkdir(parents=True, exist_ok=True)
    return path


def new_named_dir(prefix: str) -> Path:
    """A fresh timestamped directory under runs/ for a script or eval ("screen-20261023-101500"),
    created. Pipeline runs use new_run_dir, which also adds a unique suffix and a frames folder."""
    path = RUNS_DIR / (prefix + "-" + datetime.utcnow().strftime("%Y%m%d-%H%M%S"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_sha256(path: Path) -> str:
    """Hex SHA-256 of a file, read in chunks (videos are large)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(data: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    return path


def load_json(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)
