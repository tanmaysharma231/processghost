"""Local OCR for the screen timeline (RapidOCR on ONNX: no system binary, no network, so client
recordings never leave the machine -- docs/22 decision D16). Optional: if the package is missing,
get_ocr() returns None and the timeline simply leaves scene labels empty."""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:  # pragma: no cover - exercised only where the optional dependency is absent
    RapidOCR = None

# image (BGR ndarray) -> recognised text, left-to-right / top-to-bottom, joined with " | "
OcrFn = Callable[[np.ndarray], str]

_engine = None


def get_ocr() -> Optional[OcrFn]:
    global _engine
    if RapidOCR is None:
        return None
    if _engine is None:
        _engine = RapidOCR()

    def _run(img: np.ndarray) -> str:
        result, _ = _engine(img)
        return " | ".join(item[1] for item in (result or []))

    return _run
