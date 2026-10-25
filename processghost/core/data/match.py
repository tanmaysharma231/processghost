"""Does the workbook match the recording? (docs/26 flow plan)

A workbook handed to the document step may not be the file that was open in the video: an earlier or later version, an
edited copy, a different month. Answers drawn from it would then be about the wrong file, silently. This check compares what
the screen showed with what the workbook holds, with no model, before the workbook is used for anything else.

Three comparisons:
- SHEETS: the status bar's "Sheet N of M" against the workbook's sheet count. A different count is a different workbook.
- SELECTED CELLS: the content the formula bar showed for a cell, against the workbook's value there, comparing with EVERY
  reading of that cell (before and after), not the first. A cell nobody edited should be equal. A cell the recording itself
  changed may hold either state in the file, so a difference there is only a note. A cell the workbook holds empty is skipped
  (the recording may be about to fill it). A disagreement on an unchanged cell goes to a second look at the image
  (`confirm`, core/screen/verify_reads.py): only a reading the pixels back counts against the workbook.
- VISIBLE TEXT: the words read from the first full frame of each sheet, against the text on the matching workbook sheet. OCR
  garbles words, so this only reports; a single missing word is not enough to reject a workbook.

Verdict: "mismatch" if the sheet count or a selected cell's content disagrees (the workbook is not to be used), "partial" if
only some visible text is not found (it is used, and the report says it may differ), "match" if everything comparable agrees,
"unknown" if nothing could be compared.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Set, Tuple

import cv2

from core.text_grounding import norm_value
from core.data.tools import WorkbookTools
from core.models.data import MatchIssue, WorkbookMatch
from core.models.screen import ChangeRecord, ContextMap, ScreenTimeline
from core.screen.ocr import OcrFn
from core.screen.cell_trail import TrailCell
from core.screen.surfaces.spreadsheet import UI_WORDS
from core.text_grounding import compact

_SHEET_OF = re.compile(r"Sheet\s*(\d+)\s*of\s*(\d+)", re.I)
_WORD = re.compile(r"[A-Za-z]{5,}")
_MAX_REPORTED = 8
_FUZZY = 0.85  # OCR garbles a letter or two; a word this similar to a workbook word counts as found


def _sheet_total(records: List[ChangeRecord]) -> Optional[int]:
    totals = {int(m.group(2)) for r in records for m in [_SHEET_OF.search(r.chrome_after or "")] if m}
    return max(totals, key=lambda t: sum(1 for r in records if re.search(r"Sheet\s*\d+\s*of\s*%d" % t, r.chrome_after or "", re.I))) if totals else None


def _sheet_words(tools: WorkbookTools, sheet: str) -> Set[str]:
    """Lower-case words of the sheet's text cells, each cell also as one joined word (OCR often drops the spaces)."""
    words: Set[str] = set()
    for _r, _c, c in tools.sheet_cells(sheet):
        if c.value is None or c.formula is not None:
            continue
        text = str(c.value)
        words.update(w.lower() for w in _WORD.findall(text))
        words.add(compact(text))
    return words


def _is_ui(word: str) -> bool:
    """Application chrome, including OCR runs that glue two UI words together ("displaymatch")."""
    return word in UI_WORDS or any(u in word for u in UI_WORDS if len(u) >= 5)


def _found(word: str, words: Set[str]) -> bool:
    if word in words or any(word in w for w in words):
        return True
    return any(abs(len(w) - len(word)) <= 2 and SequenceMatcher(None, word, w).ratio() >= _FUZZY for w in words)


def frame_texts(contexts: List[ContextMap], timeline: ScreenTimeline, timeline_dir: Path, ocr: OcrFn) -> Dict[int, str]:
    """Sheet position -> OCR text of a full frame of that sheet, taken AFTER the last event in each of its contexts so rows the
    recording scrolled to or filled in are on screen. The context map keeps only a capped slice of the first frame, which
    can miss rows below the fold; this reads the whole frame, locally."""
    after = {e.event_id: e.after_frame for e in timeline.events}
    texts: Dict[int, str] = {}
    for ctx in contexts:
        if ctx.locator.index is None or not ctx.event_ids or ctx.event_ids[-1] not in after:
            continue
        frame = cv2.imread(str(timeline_dir / after[ctx.event_ids[-1]]))
        if frame is not None:
            texts[ctx.locator.index] = texts.get(ctx.locator.index, "") + " " + ocr(frame)
    return texts


class Disputed(NamedTuple):
    """A cell the recording did not change whose one reading disagrees with the workbook, and where to look at it again."""

    sheet: str
    cell: str
    read: str
    workbook: str
    event_id: str
    side: str  # "before" or "after": which frame of the event the reading came from


# (sheet, cell) -> "read", "workbook" or "neither": what a second look at the pixels supports. A cell missing from the answer
# was not confirmed.
Confirm = Callable[[List[Disputed]], Dict[Tuple[str, str], str]]


def _readings(records: List[ChangeRecord], trail: List[TrailCell], sheet_names: List[str]) -> Dict[Tuple[str, str], List[Tuple[str, str, str]]]:
    """(sheet, cell) -> every (content, event_id, side) the recording showed for it, before and after. The trail holds the
    after-side readings with their sheet; the before side of each record is added from its locator."""
    out: Dict[Tuple[str, str], List[Tuple[str, str, str]]] = {}
    for t in trail:
        if t.sheet and t.content:
            out.setdefault((t.sheet, t.cell), []).append((t.content, t.event_id, "after"))
    for r in records:
        loc = r.surface_before
        if loc is None or not loc.focus_content or loc.index is None or not (1 <= loc.index <= len(sheet_names)):
            continue
        out.setdefault((sheet_names[loc.index - 1], loc.focus.strip().upper()), []).append((loc.focus_content, r.event_id, "before"))
    return out


def check_match(
    records: List[ChangeRecord],
    trail: List[TrailCell],
    texts: Dict[int, str],
    tools: WorkbookTools,
    changed: Optional[Set[Tuple[str, str]]] = None,
    confirm: Optional[Confirm] = None,
) -> WorkbookMatch:
    """`changed` is the cells the recording itself changed. A workbook saved after (or before) the recording holds the other
    state of those cells, so a difference there says nothing about which file it is. `confirm` takes a second look at the
    pixels for a cell that is NOT one of them: the workbook is a hypothesis the screen must back, never the reading itself."""
    issues: List[MatchIssue] = []
    checked = 0
    changed = changed or set()

    total = _sheet_total(records)
    if total is not None:
        checked += 1
        if total != len(tools.sheetnames):
            issues.append(MatchIssue(kind="sheet_count", fatal=True, detail="the recording shows %d sheets, the workbook has %d" % (total, len(tools.sheetnames))))

    disputed: List[Disputed] = []
    for (sheet, ref), reads in _readings(records, trail, tools.sheetnames).items():
        cell = tools.cell_at(sheet, ref)
        if cell is None or (cell.value is None and cell.formula is None):
            continue
        checked += 1
        seen = {norm_value(c) for c, _e, _s in reads}
        held = cell.value if cell.value is not None else cell.formula
        # The formula bar shows a formula cell's FORMULA, a plain cell's value: either is what the file holds there.
        if seen & {norm_value(v) for v in (cell.value, cell.formula) if v is not None}:
            continue
        shown = "'%s'" % "', '".join(sorted({c[:40] for c, _e, _s in reads}))
        if (sheet, ref) in changed or len(seen) > 1:
            issues.append(MatchIssue(kind="cell_content", fatal=False, detail=(
                "%s!%s showed %s in the recording and holds '%s' in the workbook; the recording changes this cell, so the workbook may be saved from before or after it"
                % (sheet, ref, shown, str(held)[:40]))))
            continue
        content, event_id, side = reads[-1]
        disputed.append(Disputed(sheet, ref, content, str(held), event_id, side))

    answers = confirm(disputed) if (confirm is not None and disputed) else {}
    for d in disputed:
        verdict = answers.get((d.sheet, d.cell))
        detail = "%s!%s shows '%s' in the recording but '%s' in the workbook" % (d.sheet, d.cell, d.read[:40], d.workbook[:40])
        if verdict == "workbook":
            issues.append(MatchIssue(kind="cell_content", fatal=False, detail=detail + "; a second look at the image supports the workbook, so the first reading was wrong"))
        elif verdict == "neither":
            issues.append(MatchIssue(kind="cell_content", fatal=False, detail=detail + "; a second look at the image could not settle it (empty or unclear), so it is not held against the workbook"))
        else:
            issues.append(MatchIssue(kind="cell_content", fatal=True, detail=detail))

    words = found = 0
    missing: List[str] = []
    names: Set[str] = set()
    for name in tools.sheetnames:
        names.update(w.lower() for w in _WORD.findall(name))
        names.add(compact(name))
    for index, text in sorted(texts.items()):
        if not (1 <= index <= len(tools.sheetnames)) or not text:
            continue
        sheet_words = _sheet_words(tools, tools.sheetnames[index - 1]) | names  # the tab strip shows every sheet's name
        for w in sorted(w for w in {w.lower() for w in _WORD.findall(text)} if not _is_ui(w)):
            words += 1
            if _found(w, sheet_words):
                found += 1
            else:
                missing.append(w)
    recall = found / words if words else None
    if recall is not None:
        checked += words
        if missing:
            issues.append(MatchIssue(kind="text_missing", fatal=False, detail="%d of %d words visible in the recording are not in the workbook (OCR can garble a word, so this is a warning, not a rejection): %s" % (len(missing), words, ", ".join(missing[:_MAX_REPORTED]))))

    if not checked:
        verdict = "unknown"
    elif any(i.fatal for i in issues):
        verdict = "mismatch"
    elif issues:
        verdict = "partial"
    else:
        verdict = "match"
    return WorkbookMatch(verdict=verdict, checked=checked, issues=issues, label_recall=recall)
