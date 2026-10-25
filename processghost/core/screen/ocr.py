"""Local OCR for the screen timeline (RapidOCR on ONNX: no system binary, no network, so client
recordings never leave the machine -- docs/22 decision D16). Optional: if the package is missing,
get_ocr() returns None and the timeline simply leaves scene labels empty."""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

import numpy as np

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:  # pragma: no cover - exercised only where the optional dependency is absent
    RapidOCR = None

# image (BGR ndarray) -> recognised text, left-to-right / top-to-bottom, joined with " | "
OcrFn = Callable[[np.ndarray], str]
# image -> [(text, (x0, y0, x1, y1))] in the image's own pixels, for finding WHERE a piece of text is
OcrLinesFn = Callable[[np.ndarray], List[Tuple[str, Tuple[int, int, int, int]]]]

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


def get_ocr_lines() -> Optional[OcrLinesFn]:
    """The same engine as get_ocr, returning each text line with its bounding box."""
    global _engine
    if RapidOCR is None:
        return None
    if _engine is None:
        _engine = RapidOCR()

    def _run(img: np.ndarray) -> List[Tuple[str, Tuple[int, int, int, int]]]:
        result, _ = _engine(img)
        lines = []
        for points, text, *_rest in (result or []):
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            lines.append((text, (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))))
        return lines

    return _run
