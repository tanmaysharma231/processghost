"""Generic web-page surface profile. Signals: a URL, a browser product name, a "A > B > C" breadcrumb, a
"Page title - Browser" window title. UNPROVEN on real recordings: it is built from the structure of
browser chrome and exercised only on synthetic strings until a real web recording exists. A web-hosted
spreadsheet scores as web when its URL and browser name outweigh the spreadsheet signals."""
from __future__ import annotations

import re

from core.models.screen import Locator
from core.screen.surfaces.base import SurfaceProfile

_TLDS = "com|org|net|io|in|co|app|dev|gov|edu|ai|cloud"
_URL = re.compile(rf"(?<![\w@.])(?:https?://)?((?:[a-z0-9-]+\.)+(?:{_TLDS}))(/[^\s|]*)?(?![\w.])", re.I)
_BROWSER = re.compile(r"(Google\s*Chrome|Microsoft\s*Edge|Mozilla\s*Firefox|Firefox|Brave|Safari)", re.I)
_TITLE = re.compile(
    r"([^|]{3,120}?)\s+[-\u2013]\s+(?:Google\s*Chrome|Microsoft\s*Edge|Mozilla\s*Firefox|Firefox|Brave|Safari)", re.I
)
_BREADCRUMB = re.compile(r"([^|>]{2,40}(?:\s*>\s*[^|>]{2,40}){1,6})")


class WebProfile(SurfaceProfile):
    surface = "web"

    def score(self, chrome: str) -> float:
        s = 0.0
        s += 0.5 if _URL.search(chrome) else 0.0
        s += 0.3 if _BROWSER.search(chrome) else 0.0
        s += 0.2 if _BREADCRUMB.search(chrome) else 0.0
        s += 0.1 if _TITLE.search(chrome) else 0.0
        return min(1.0, s)

    def locate(self, chrome: str) -> Locator:
        loc = Locator(surface="web", confidence=self.score(chrome))
        evidence = []
        url = _URL.search(chrome)
        if url:
            loc.document = url.group(1).lower()
            loc.location = (url.group(2) or "").rstrip("/")
            evidence.append(url.group(0))
        browser = _BROWSER.search(chrome)
        if browser:
            loc.app = browser.group(1).strip()
            evidence.append(loc.app)
        title = _TITLE.search(chrome)
        crumb = _BREADCRUMB.search(chrome)
        if title:
            loc.title = title.group(1).strip()
            evidence.append(loc.title)
        elif crumb:
            loc.title = crumb.group(1).strip()
        if crumb:
            evidence.append(crumb.group(1).strip())
        loc.evidence = evidence
        return loc
