"""Pick the best-scoring surface profile for a chrome string; below SURFACE_MIN_CONFIDENCE fall back to
the generic profile. Add a profile by appending it to PROFILES."""
from __future__ import annotations

from typing import List

from core import config
from core.models.screen import Locator
from core.screen.surfaces.base import SurfaceProfile
from core.screen.surfaces.generic import GenericProfile
from core.screen.surfaces.spreadsheet import SpreadsheetProfile
from core.screen.surfaces.web import WebProfile

PROFILES: List[SurfaceProfile] = [SpreadsheetProfile(), WebProfile()]
_GENERIC = GenericProfile()


def locate(chrome: str) -> Locator:
    best = max(PROFILES, key=lambda p: p.score(chrome))
    if best.score(chrome) >= config.SURFACE_MIN_CONFIDENCE:
        return best.locate(chrome)
    return _GENERIC.locate(chrome)
