"""Spreadsheet surface profile. Signals: the name box next to the formula-bar glyph ("C7 | fx | ..."),
the status bar's "Sheet N of M", a page-style sheet name, a "Sum:" / "Average:" selection summary, a
workbook file name. Written from the structure of spreadsheet chrome, not from any one recording."""
from __future__ import annotations

import re

from core.models.screen import Locator
from core.screen.surfaces.base import SurfaceProfile

# The glyph is read as "fx" plus whatever OCR makes of the cancel/accept icons next to it ("fx2.=", "fxX"): up to three
# non-space, non-separator characters after "fx" are glyph debris, not content.
_NAME_BOX_FX = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,3}\d{1,7})\s*\|?\s*[Il1]?fx[^|\s]{0,3}\s*\|?\s*(.*)", re.I | re.S)
_SHEET_OF = re.compile(r"Sheet\s*(\d+)\s*of\s*(\d+)", re.I)
_PAGE_STYLE = re.compile(r"PageStyle_(\w+)")
_SELECTION_SUMMARY = re.compile(r"(?:Sum|Average)\s*[:=]", re.I)
_DOCUMENT = re.compile(r"([A-Za-z0-9_ .()\-]{1,80}\.(?:xlsx|xlsm|xls|ods|csv))", re.I)
_APP = re.compile(r"(LibreOffice\s*Calc|Microsoft\s*Excel|Excel|Google\s*Sheets)", re.I)
# Fragments that follow the formula bar in the chrome strips and are never cell content: the status bar,
# find-toolbar words, language and zoom.
_NOT_CONTENT = re.compile(r"^\s*(?:Sheet\s*\d+\s*of|Sum\s*[:=]|Average\s*[:=]|PageStyle_|Find|Replace|English|\d+\s*%)", re.I)
_CELL_REF_FRAGMENT = re.compile(r"[A-Za-z]{1,3}\d{1,7}")
_LEADING_JUNK = re.compile(r"^[^A-Za-z0-9=(\-+\"']+")
_CONTENT_FRAGMENTS = 2  # look at most this many fragments past the formula-bar glyph

# Words of the application itself (menus, toolbar, status bar, dialogs), found in any full-frame OCR of a spreadsheet window.
# They are not the document's text, so comparing a frame with a workbook ignores them.
UI_WORDS = frozenset({
    "arial", "format", "insert", "styles", "stylessheet", "sheet", "tools", "window", "help", "data", "view", "edit", "file", "calc",
    "find", "findall", "formatted", "display", "matchcase", "match", "case", "page", "pagestyle", "english", "average", "zoom",
    "default", "normal", "bold", "italic", "underline", "number", "currency", "percent", "border", "borders", "background",
    "alignment", "wrap", "merge", "freeze", "filter", "sort", "chart", "image", "table", "form", "macros", "script", "extensions",
    "styles", "libreoffice", "excel", "sheets", "cells", "column", "columns", "rows", "paste", "clipboard", "undo", "redo",
})


class SpreadsheetProfile(SurfaceProfile):
    surface = "spreadsheet"

    @staticmethod
    def _content(after_fx: str) -> str:
        """First usable fragment after the formula-bar glyph: skip fragments that are only OCR debris from
        the glyph, and anything that is clearly toolbar or status text (an empty cell is followed by those)."""
        for frag in after_fx.split(" | ")[:_CONTENT_FRAGMENTS]:
            cleaned = _LEADING_JUNK.sub("", frag.strip())
            if not any(c.isalnum() for c in cleaned) or _NOT_CONTENT.match(cleaned):
                continue
            return cleaned
        return ""

    def score(self, chrome: str) -> float:
        s = 0.0
        s += 0.45 if _NAME_BOX_FX.search(chrome) else 0.0
        s += 0.35 if _SHEET_OF.search(chrome) else 0.0
        s += 0.15 if _PAGE_STYLE.search(chrome) else 0.0
        s += 0.10 if _SELECTION_SUMMARY.search(chrome) else 0.0
        s += 0.10 if _DOCUMENT.search(chrome) else 0.0
        return min(1.0, s)

    def locate(self, chrome: str) -> Locator:
        loc = Locator(surface="spreadsheet", confidence=self.score(chrome))
        evidence = []

        fx = _NAME_BOX_FX.search(chrome)
        if fx:
            loc.focus = fx.group(1).upper()
            evidence.append(fx.group(1))
            content = self._content(fx.group(2))
            if content:
                loc.focus_content = content
                evidence.append(content)
        else:
            # OCR often garbles or drops the formula-bar glyph, so a lone cell reference is a weaker signal
            # for the selected cell. Content is not claimed in this mode: guessing it would be a guess.
            ref = next((f.strip() for f in chrome.split(" | ") if _CELL_REF_FRAGMENT.fullmatch(f.strip())), None)
            if ref:
                loc.focus = ref.upper()
                evidence.append(ref)

        sheet_of = _SHEET_OF.search(chrome)
        if sheet_of:
            loc.index = int(sheet_of.group(1))
            evidence.append(sheet_of.group(0))

        page_style = _PAGE_STYLE.search(chrome)
        if page_style:
            loc.location = page_style.group(1)
            evidence.append(page_style.group(0))
        elif loc.index is not None:
            loc.location = f"Sheet {loc.index}"

        doc = _DOCUMENT.search(chrome)
        if doc:
            loc.document = doc.group(1).strip()
            evidence.append(loc.document)
        app = _APP.search(chrome)
        if app:
            loc.app = app.group(1).strip()
            evidence.append(loc.app)

        loc.evidence = evidence
        return loc
