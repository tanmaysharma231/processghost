"""Fallback surface profile: the first fragment of the top strip (usually a window or page title) and
nothing else. Low confidence by construction, so diff_locators reports "unknown" rather than guess."""
from __future__ import annotations

from core.models.screen import Locator
from core.screen.surfaces.base import SurfaceProfile

_TITLE_MAX_CHARS = 80


class GenericProfile(SurfaceProfile):
    surface = "generic"

    def score(self, chrome: str) -> float:
        return 0.2 if chrome.strip() else 0.0

    def locate(self, chrome: str) -> Locator:
        first = chrome.split(" | ")[0].strip()[:_TITLE_MAX_CHARS]
        return Locator(surface="generic", title=first, evidence=[first] if first else [], confidence=self.score(chrome))
