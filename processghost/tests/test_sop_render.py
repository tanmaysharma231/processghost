"""Unit tests for report/sop_render.py -- deterministic markdown rendering from a SOPDocument into
four sections: the workflow as spoken, what the pipeline assumed, what is missing, and the
questions that follow. See docs/15-provenance-and-doc-format.md for the format spec and why the
old inline-everything layout was replaced."""
from __future__ import annotations

from core.models.sop import (
    AppendixSegment,
    Attribution,
    Flag,
    InferredClause,
    Resolution,
    SOPDocument,
    SOPStep,
    StepTransition,
)
from report.sop_render import fmt_ts, render_sop_md, write_sop


def test_fmt_ts_formats_minutes_and_seconds():
    assert fmt_ts(0.0) == "0:00"
    assert fmt_ts(12.0) == "0:12"
    assert fmt_ts(65.0) == "1:05"
    assert fmt_ts(3725.0) == "1:02:05"


def _doc_with_one_step(**doc_overrides) -> SOPDocument:
    step = SOPStep(
        step_id="step-01",
        order=1,
        title="Open the reference sheet",
        instruction="Open the reference sheet.",
        attributions=[Attribution(kind="said", t_s=12.0, segment_ids=[0])],
    )
    base = dict(run_id="run1", steps=[step])
    base.update(doc_overrides)
    return SOPDocument(**base)


def _two_step_doc(**doc_overrides) -> SOPDocument:
    steps = [
        SOPStep(
            step_id="step-01", order=1, title="Open the reference sheet",
            instruction="Open the reference sheet.",
            attributions=[Attribution(kind="said", t_s=0.0, segment_ids=[0])],
        ),
        SOPStep(
            step_id="step-02", order=2, title="Paste it",
            instruction="Paste it here.",
            attributions=[Attribution(kind="said", t_s=6.0, segment_ids=[1])],
        ),
    ]
    base = dict(run_id="run1", steps=steps)
    base.update(doc_overrides)
    return SOPDocument(**base)


def test_render_includes_said_attribution_for_every_step():
    doc = _doc_with_one_step()
    md = render_sop_md(doc, video_name="video.mp4")
    assert "[said @0:12]" in md


def test_render_is_deterministic():
    doc = _doc_with_one_step()
    md1 = render_sop_md(doc, video_name="video.mp4")
    md2 = render_sop_md(doc, video_name="video.mp4")
    assert md1 == md2


def test_appendix_segments_rendered_with_timestamps():
    doc = _doc_with_one_step(
        appendix_segments=[AppendixSegment(segment_id=1, t_s=21.0, text="uh, okay", reason="small talk")]
    )
    md = render_sop_md(doc, video_name="video.mp4")
    assert "@0:21" in md
    assert "uh, okay" in md
    assert "small talk" in md


def test_timing_degraded_banner():
    doc = _doc_with_one_step(timing_degraded=True)
    md = render_sop_md(doc, video_name="video.mp4")
    assert "TIMING DEGRADED" in md


def test_no_timing_degraded_banner_by_default():
    doc = _doc_with_one_step()
    md = render_sop_md(doc, video_name="video.mp4")
    assert "TIMING DEGRADED" not in md


def test_review_failed_banner_rendered():
    doc = _doc_with_one_step(review_failed=True)
    md = render_sop_md(doc, video_name="video.mp4")
    assert "REVIEW FAILED" in md


def test_no_steps_case_still_renders_appendix():
    doc = SOPDocument(
        run_id="run1",
        appendix_segments=[AppendixSegment(segment_id=0, t_s=0.0, text="hello", reason="small talk")],
    )
    md = render_sop_md(doc, video_name="video.mp4")
    assert "No steps were extracted" in md
    assert "hello" in md


# ---- Section 1: the workflow, as spoken --------------------------------------------------------


def test_section_1_header_present():
    doc = _doc_with_one_step()
    md = render_sop_md(doc, video_name="video.mp4")
    assert "## 1. The workflow, as spoken" in md


def test_section_1_never_shows_flag_tags_inline():
    """Section 1 is meant to be pure narration -- flag callouts belong in sections 3/4 only."""
    doc = _two_step_doc(
        flags=[
            Flag(
                flag_id="FLAG-01", family="ambiguity", question="Which sheet?",
                severity="blocking", window_segment_ids=[0], step_id="step-01",
            )
        ]
    )
    md = render_sop_md(doc, video_name="video.mp4")
    section_1 = md.split("\n## 2.")[0]
    assert "FLAG-01" not in section_1


def test_wholly_inferred_step_marked_not_narrated_in_section_1():
    """A Checker-inserted structural step (attribution kind='inferred') must not read as if it
    was said -- that is exactly the ambiguity this section exists to avoid."""
    doc = _two_step_doc()
    inserted = SOPStep(
        step_id="step-ins-FLAG-01", order=3, title="Confirm the total",
        instruction="Confirm the total updated.",
        attributions=[Attribution(kind="inferred", t_s=15.0)],
        flag_ids=["FLAG-01"],
    )
    doc = doc.model_copy(update={"steps": doc.steps + [inserted]})
    md = render_sop_md(doc, video_name="video.mp4")
    section_1 = md.split("\n## 2.")[0]
    assert "not narrated" in section_1


def test_mermaid_block_emitted_for_covered_transition():
    doc = _two_step_doc(
        transitions=[
            StepTransition(
                from_step_id="step-01", to_step_id="step-02",
                carries="the value is copied", quote="paste it here",
            )
        ]
    )
    md = render_sop_md(doc, video_name="video.mp4")
    assert "```mermaid" in md
    assert "flowchart TD" in md
    assert "step-01 --> step-02" in md


def test_mermaid_block_draws_broken_arrow_for_missing_transition():
    doc = _two_step_doc(
        flags=[
            Flag(
                flag_id="FLAG-01", family="structural",
                question="What happens between step 1 and step 2?",
                severity="blocking", window_segment_ids=[0, 1],
                between_step_ids=("step-01", "step-02"),
            )
        ]
    )
    md = render_sop_md(doc, video_name="video.mp4")
    assert "```mermaid" in md
    assert "step-01 -.FLAG-01.-> step-02" in md


def test_instruction_text_stays_byte_stable_next_to_a_flag():
    """Flags never rewrite the narrated text -- docs/15's augment-don't-rewrite rule."""
    doc = _two_step_doc(
        flags=[
            Flag(
                flag_id="FLAG-01", family="ambiguity", question="Which sheet?",
                severity="blocking", window_segment_ids=[0], step_id="step-01",
            )
        ]
    )
    md = render_sop_md(doc, video_name="video.mp4")
    assert "Open the reference sheet." in md


# ---- Section 2: what we assumed ----------------------------------------------------------------


def test_section_2_empty_when_nothing_assumed():
    doc = _doc_with_one_step()
    md = render_sop_md(doc, video_name="video.mp4")
    assert "Nothing was assumed" in md


def test_section_2_renders_inferred_clause_with_its_flag_id():
    doc = _two_step_doc(
        flags=[
            Flag(
                flag_id="FLAG-01", family="ambiguity", question="Which sheet?",
                severity="blocking", window_segment_ids=[0], step_id="step-01", status="resolved",
            )
        ]
    )
    step = doc.steps[0].model_copy(
        update={
            "inferred_clauses": [
                InferredClause(text="it's the 'March' sheet", t_s=3.0, flag_id="FLAG-01")
            ]
        }
    )
    doc = doc.model_copy(update={"steps": [step, doc.steps[1]]})
    md = render_sop_md(doc, video_name="video.mp4")
    section_2 = md.split("\n## 2.")[1].split("\n## 3.")[0]
    assert "it's the 'March' sheet" in section_2
    assert "FLAG-01" in section_2


def test_section_2_renders_inserted_step():
    doc = _two_step_doc()
    inserted = SOPStep(
        step_id="step-ins-FLAG-01", order=3, title="Confirm the total",
        instruction="Confirm the total updated.",
        attributions=[Attribution(kind="inferred", t_s=15.0)],
        flag_ids=["FLAG-01"],
    )
    doc = doc.model_copy(update={"steps": doc.steps + [inserted]})
    md = render_sop_md(doc, video_name="video.mp4")
    section_2 = md.split("\n## 2.")[1].split("\n## 3.")[0]
    assert "Confirm the total" in section_2
    assert "New step" in section_2


# ---- Section 3: what is missing, or might be ----------------------------------------------------


def test_section_3_empty_when_nothing_missing():
    doc = _doc_with_one_step()
    md = render_sop_md(doc, video_name="video.mp4")
    assert "Nothing is missing" in md


def test_contradicted_flag_shows_both_versions():
    doc = _two_step_doc(
        flags=[
            Flag(
                flag_id="FLAG-01", family="ambiguity", question="Does the total update automatically?",
                severity="blocking", window_segment_ids=[0], step_id="step-01", status="contradicted",
            )
        ],
        resolutions=[
            Resolution(
                flag_id="FLAG-01", verdict="contradicted",
                narration_version="the total updates automatically",
                screen_version="the total was unchanged after the action",
                evidence=[{"frame_path": "f.png", "t_s": 1.0}],
            )
        ],
    )
    md = render_sop_md(doc, video_name="video.mp4")
    section_3 = md.split("\n## 3.")[1].split("\n## 4.")[0]
    assert "the total updates automatically" in section_3
    assert "the total was unchanged after the action" in section_3


def test_unresolvable_flag_shows_reason():
    doc = _two_step_doc(
        flags=[
            Flag(
                flag_id="FLAG-01", family="ambiguity", question="Which button?",
                severity="caution", window_segment_ids=[0], step_id="step-01", status="unresolvable",
            )
        ],
        resolutions=[
            Resolution(flag_id="FLAG-01", verdict="unresolvable", reason="nothing legible in frame")
        ],
    )
    md = render_sop_md(doc, video_name="video.mp4")
    section_3 = md.split("\n## 3.")[1].split("\n## 4.")[0]
    assert "nothing legible in frame" in section_3


def test_check_failed_and_unchecked_labelled_as_pipeline_limitation_not_a_finding():
    doc = _two_step_doc(
        flags=[
            Flag(
                flag_id="FLAG-01", family="ambiguity", question="Which sheet?",
                severity="blocking", window_segment_ids=[0], step_id="step-01", status="check_failed",
            ),
            Flag(
                flag_id="FLAG-02", family="ambiguity", question="Which column?",
                severity="note", window_segment_ids=[1], step_id="step-02", status="unchecked",
            ),
        ],
    )
    md = render_sop_md(doc, video_name="video.mp4")
    section_3 = md.split("\n## 3.")[1].split("\n## 4.")[0]
    assert "pipeline limitation" in section_3
    assert "FLAG-01" in section_3 and "FLAG-02" in section_3


def test_reliability_warning_when_transitions_empty_but_ambiguity_flags_rich():
    doc = _two_step_doc(
        flags=[
            Flag(
                flag_id=f"FLAG-0{i}", family="ambiguity", question=f"Q{i}?",
                severity="caution", window_segment_ids=[0], step_id="step-01",
            )
            for i in range(1, 3)
        ],
        transitions=[],
    )
    md = render_sop_md(doc, video_name="video.mp4")
    assert "REVIEWER SIGNALS DISAGREE" in md


def test_no_reliability_warning_when_transitions_present():
    doc = _two_step_doc(
        flags=[
            Flag(
                flag_id="FLAG-01", family="ambiguity", question="Q?",
                severity="caution", window_segment_ids=[0], step_id="step-01",
            )
        ],
        transitions=[
            StepTransition(
                from_step_id="step-01", to_step_id="step-02",
                carries="the value is copied", quote="paste it here",
            )
        ],
    )
    md = render_sop_md(doc, video_name="video.mp4")
    assert "REVIEWER SIGNALS DISAGREE" not in md


# ---- Section 4: questions to ask -----------------------------------------------------------------


def test_section_4_empty_when_no_open_questions():
    doc = _doc_with_one_step()
    md = render_sop_md(doc, video_name="video.mp4")
    assert "No open questions" in md


def test_section_4_lists_every_non_resolved_flag_ordered_by_severity():
    doc = _two_step_doc(
        flags=[
            Flag(
                flag_id="FLAG-01", family="ambiguity", question="Low priority?",
                severity="note", window_segment_ids=[0], step_id="step-01",
            ),
            Flag(
                flag_id="FLAG-02", family="ambiguity", question="Urgent?",
                severity="blocking", window_segment_ids=[1], step_id="step-02",
            ),
        ]
    )
    md = render_sop_md(doc, video_name="video.mp4")
    section_4 = md.split("\n## 4.")[1]
    assert section_4.index("Urgent?") < section_4.index("Low priority?")


def test_resolved_flag_never_appears_in_section_4():
    doc = _two_step_doc(
        flags=[
            Flag(
                flag_id="FLAG-01", family="ambiguity", question="Which sheet?",
                severity="blocking", window_segment_ids=[0], step_id="step-01", status="resolved",
            )
        ]
    )
    md = render_sop_md(doc, video_name="video.mp4")
    section_4 = md.split("\n## 4.")[1]
    assert "FLAG-01" not in section_4


def test_flags_never_touched_before_this_change_now_render():
    """Regression guard: a flag with no matching family branch (an unattached ambiguity flag)
    still shows up somewhere in the document rather than silently vanishing."""
    doc = _two_step_doc(
        flags=[
            Flag(
                flag_id="FLAG-01", family="ambiguity", question="Which column?",
                severity="caution", window_segment_ids=[1], step_id="step-02",
            )
        ]
    )
    md = render_sop_md(doc, video_name="video.mp4")
    assert "FLAG-01" in md


def test_write_sop_persists_file(tmp_path):
    doc = _doc_with_one_step()
    path = write_sop(doc, tmp_path, video_name="video.mp4", filename="sop_draft.md")
    assert path.exists()
    assert path.name == "sop_draft.md"
    assert "[said @0:12]" in path.read_text(encoding="utf-8")
