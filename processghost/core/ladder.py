"""The evidence ladder for one question: video, then document, then the person who recorded it.

Pure functions over the SOPDocument. The video rung is the Reconciler, the anchors and the Checker; the
document rung is core/data/. This module records what each did (so a question that reaches the person carries
what was already tried), decides which questions the document rung should take, and words the questions that
remain for the person who recorded it. Nothing here calls a model.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from core import config
from core.models.data import RungAttempt
from core.models.sop import Flag, Resolution, SOPDocument

_SEVERITY_ORDER = {"blocking": 0, "caution": 1, "note": 2}
_UNRESOLVED = ("open", "unresolvable", "unchecked", "check_failed")
_SLOT_PRIORITY = {"key": 0, "effect": 0, "source": 1, "target": 1}  # rules live in the document; the screen shows values


def _video_attempt(flag: Flag, res: Resolution | None) -> RungAttempt:
    if flag.status == "resolved" and res is not None:
        return RungAttempt(
            rung="video", outcome="answered", reason=res.reason or (res.inferred_text or ""),
            event_ids=list(res.event_ids) or list(flag.event_ids),
        )
    if flag.status == "contradicted":
        return RungAttempt(rung="video", outcome="contradicted", reason=(res.reason if res else ""), event_ids=list(flag.event_ids))
    if flag.status == "unresolvable":
        return RungAttempt(rung="video", outcome="no_answer", reason=(res.reason if res else "") or "nothing on screen answered it", event_ids=list(flag.event_ids))
    reason = {"unchecked": "over this run's flag cap or spend budget", "check_failed": "the check itself failed"}.get(flag.status, "not checked")
    return RungAttempt(rung="video", outcome="skipped", reason=reason)


def record_video_rungs(doc: SOPDocument) -> SOPDocument:
    """Attach the video rung's outcome to every flag that has none yet. Idempotent."""
    res_by_flag: Dict[str, Resolution] = {r.flag_id: r for r in doc.resolutions}
    flags = []
    for f in doc.flags:
        if any(r.rung == "video" for r in f.rungs):
            flags.append(f)
        else:
            flags.append(f.model_copy(update={"rungs": [_video_attempt(f, res_by_flag.get(f.flag_id))] + list(f.rungs)}))
    return doc.model_copy(update={"flags": flags})


def route_to_document(doc: SOPDocument, has_workbook: bool) -> Tuple[List[Flag], List[Flag]]:
    """(routed, overflow): the questions the document rung should take, most useful first, at most
    DATA_MAX_ROUTED_FLAGS, and the eligible ones the cap left out (recorded as skipped, never dropped silently).

    - only the four slot questions by default (config.DATA_ROUTE_NON_SLOT): a general question ("what happens between these steps?")
      is rarely a question the workbook can answer, and on the first real run those lookups cost money and answered nothing;
    - any question the video did not answer (open, unresolvable, unchecked, check_failed);
    - a question the video resolved that would block a builder if wrong (a cross-check: the screen can show one
      value while the real rule differs).
    Rule and key questions go first, because rules live in the document and the screen only shows values. A
    question the document already attempted is not sent again. With no workbook nothing is routed."""
    if not has_workbook:
        return [], []
    eligible = [
        f for f in doc.flags
        if not any(r.rung == "document" for r in f.rungs)
        and (f.slot is not None or config.DATA_ROUTE_NON_SLOT)
        and (f.status in _UNRESOLVED or (f.status == "resolved" and f.severity == "blocking"))
    ]
    eligible.sort(key=lambda f: (_SLOT_PRIORITY.get(f.slot or "", 2), _SEVERITY_ORDER.get(f.severity, 3), f.flag_id))
    return eligible[: config.DATA_MAX_ROUTED_FLAGS], eligible[config.DATA_MAX_ROUTED_FLAGS :]


def sme_text(flag: Flag) -> Tuple[str, str]:
    """(wording, style) for a question that reaches the person who recorded the process. A question the document
    could only narrow to a few candidates is worded as a confirmation, which takes seconds to answer; a conflict
    between sources is worded as a conflict, never settled here."""
    document = next((r for r in flag.rungs if r.rung == "document"), None)
    if document is not None and document.outcome == "candidates" and document.candidates:
        return "%s Which is it: %s?" % (flag.question, " or ".join(document.candidates)), "confirm"
    if flag.status == "contradicted":
        return "The sources disagree on this: %s" % flag.question, "conflict"
    return flag.question, "open"


def sme_questions(doc: SOPDocument) -> List[dict]:
    """What is left for the person who recorded the process, most severe first, each with what was already tried.
    A question the document could only narrow to a few candidates is worded as a confirmation, which takes them
    seconds to answer; a conflict between sources is worded as a conflict, never settled here."""
    out = []
    for f in doc.flags:
        if f.status == "resolved":
            continue
        if f.status in ("unchecked", "check_failed"):
            continue  # a pipeline limitation, not a question for the person
        text, style = sme_text(f)
        out.append({
            "flag_id": f.flag_id, "severity": f.severity, "style": style, "question": text, "slot": f.slot,
            "step_id": f.step_id, "tried": [r.model_dump(mode="json") for r in f.rungs],
        })
    out.sort(key=lambda q: (_SEVERITY_ORDER.get(q["severity"], 3), q["flag_id"]))
    return out
