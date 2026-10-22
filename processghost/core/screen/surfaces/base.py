"""Surface profile interface and the locator comparison.

A profile recognises one kind of application from the OCR text of a frame's chrome strips and extracts a
`Locator`. Comparing the locators of the before and after frames of a screen event says, without a model,
whether the application, document, sheet or page changed. When a profile is not confident the answer is
"unknown", never a guess.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from core.models.screen import Locator, SurfaceChangeKind
from core.text_grounding import compact


class SurfaceProfile(ABC):
    surface: str

    @abstractmethod
    def score(self, chrome: str) -> float:
        """0..1: how strongly `chrome` looks like this kind of application."""

    @abstractmethod
    def locate(self, chrome: str) -> Locator:
        """Extract the locator. Every non-empty field must be a substring of `chrome` (modulo whitespace)."""


def _differs(a: str, b: str) -> bool:
    return compact(a) != compact(b)


def diff_locators(a: Optional[Locator], b: Optional[Locator]) -> SurfaceChangeKind:
    if a is None or b is None:
        return "unknown"
    if a.surface != b.surface:
        # A generic reading next to a specific one may only mean OCR missed the signals in one frame.
        return "unknown" if "generic" in (a.surface, b.surface) else "app_switch"
    if a.surface == "generic":
        if not a.title or not b.title:
            return "unknown"
        return "same_surface_edit" if not _differs(a.title, b.title) else "unknown"
    if a.app and b.app and _differs(a.app, b.app):
        return "app_switch"
    if a.document and b.document and _differs(a.document, b.document):
        return "document_switch"
    if a.surface == "spreadsheet":
        if a.index is not None and b.index is not None:
            moved = a.index != b.index
        elif a.location and b.location:
            moved = _differs(a.location, b.location)
        else:
            return "unknown"
        return "sheet_switch" if moved else "same_surface_edit"
    known = moved = False
    for x, y in ((a.location, b.location), (a.title, b.title)):
        if x and y:
            known = True
            moved = moved or _differs(x, y)
    if not known:
        return "unknown"
    return "page_navigation" if moved else "same_surface_edit"
