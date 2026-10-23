"""Run manifest: everything needed to say what produced a run's output and to judge whether two runs
are comparable. Written to runs/<run_id>/manifest.json at the start of every run.

Records the code version (git commit, and whether the tree had uncommitted changes), a hash of every
prompt, the models and sampling settings, the tunable thresholds, library versions and the input's
hash. Two runs with the same manifest and the same input are expected to differ only by model
nondeterminism; a differing prompt hash or threshold explains a different result without guessing.
"""
from __future__ import annotations

import hashlib
import platform
import subprocess
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Dict

from core import config
from core.io.storage import file_sha256
from core.llm import prompts

_TRACKED_PACKAGES = ("openai", "langchain-openai", "langchain-core", "pydantic", "opencv-python", "rapidocr-onnxruntime")
_CONFIG_PREFIXES = ("MODEL_", "ANTHROPIC_MODEL", "LLM_", "MAX_CHECKED", "CHECKER_", "SCREEN_", "RETRY_", "VISION_", "CACHE_ENABLED")


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, check=True, cwd=config.ROOT)
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 - no git, not a repo, etc.: the manifest just says so
        return None


def _version(pkg: str) -> str | None:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def prompt_hashes() -> Dict[str, str]:
    return {
        name: hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        for name, value in sorted(vars(prompts).items())
        if name.isupper() and isinstance(value, str)
    }


def build_manifest(video_path: Path, provider: str | None = None) -> Dict[str, Any]:
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--", ".")
    return {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "git_commit": commit,
        "git_dirty": bool(status) if status is not None else None,
        "python": platform.python_version(),
        "packages": {p: _version(p) for p in _TRACKED_PACKAGES},
        "provider": provider or config.LLM_PROVIDER,
        "input": {"name": video_path.name, "sha256": file_sha256(video_path), "bytes": video_path.stat().st_size},
        "prompt_hashes": prompt_hashes(),
        "config": {
            k: (list(v) if isinstance(v, tuple) else v)
            for k, v in sorted(vars(config).items())
            if k.startswith(_CONFIG_PREFIXES) and isinstance(v, (int, float, str, bool, tuple))
        },
    }
