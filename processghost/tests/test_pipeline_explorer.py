"""The decision register and the pipeline explorer are generated from scripts/pipeline_spec.py plus the
code. These tests check the spec is internally consistent, that nothing in the generated page can drift
from the code (every prompt is present verbatim), and that the committed files are current."""
from __future__ import annotations

import json
import re

from core import config
from core.llm import prompts
from scripts import build_pipeline_explorer as build
from scripts import pipeline_spec as spec

STAGE_IDS = {s["id"] for s in spec.all_stages()}


def _payload(html: str) -> dict:
    raw = re.search(r'<script type="application/json" id="pg-data">(.*?)</script>', html, re.S).group(1)
    return json.loads(raw.replace("<\\/", "</"))


def test_every_setting_and_prompt_named_in_the_spec_exists():
    for s in spec.all_stages():
        for k in s["knobs"]:
            assert hasattr(config, k), f"{s['id']}: unknown setting {k}"
        for p in s["prompts"]:
            assert hasattr(prompts, p), f"{s['id']}: unknown prompt {p}"


def test_every_prompt_in_the_code_is_shown_on_exactly_one_stage():
    shown = [p for s in spec.all_stages() for p in s["prompts"]]
    in_code = [n for n, v in vars(prompts).items() if n.isupper() and isinstance(v, str)]
    assert sorted(shown) == sorted(in_code), "a prompt exists in code but not in the explorer (or the reverse)"


def test_branch_tones_and_stage_references_are_valid():
    for s in spec.all_stages():
        assert s["tree"], f"{s['id']} has no decision tree"
        for step in s["tree"]:
            for b in step["branches"]:
                assert b["tone"] in {"ok", "warn", "stop"}
    for d in spec.all_decisions():
        assert set(d["stages"]) <= STAGE_IDS, d["id"]
        assert d["status"] in {"built", "partial", "planned"}, d["id"]
    for q in spec.OPEN_QUESTIONS:
        assert set(q["stages"]) <= STAGE_IDS, q["id"]


def test_all_decision_tables_are_picked_up():
    ids = [d["id"] for d in spec.all_decisions()]
    assert [f"D{i}" for i in range(1, 21)] == [i for i in ids if i.startswith("D")]
    assert len([i for i in ids if i.startswith("ADR-")]) == 9
    assert len(ids) == len(set(ids)), "duplicate decision id"


def test_html_carries_every_prompt_verbatim_and_cannot_break_out_of_its_script_tag():
    html = build.render_document()
    assert html.isascii()
    data = _payload(html)
    by_name = {p["name"]: p["text"] for s in data["stages"] for p in s["prompts"]}
    for name, value in vars(prompts).items():
        if name.isupper() and isinstance(value, str):
            assert by_name[name] == value
    # prompts contain "</role>", "</steps>" and so on: none may appear raw inside the JSON script
    inside = re.search(r'id="pg-data">(.*?)</script>', html, re.S).group(1)
    assert "</" not in inside


def test_html_fragment_has_no_document_wrapper_and_a_name_for_a_title():
    frag = build.render_fragment()
    assert "<html" not in frag and "<body" not in frag and "<!doctype" not in frag.lower()
    assert re.search(r"<title>[^<]{3,40}</title>", frag)
    assert "prefers-color-scheme: dark" in frag and 'data-theme="dark"' in frag


def test_markdown_lists_every_stage_and_decision():
    md = build.render_markdown()
    for s in spec.all_stages():
        assert f"- {s['name']}" in md or f"{s['name']}" in md
    for d in spec.all_decisions():
        assert f"### {d['id']} - " in md
    for q in spec.OPEN_QUESTIONS:
        assert f"### {q['id']} - " in md


def test_committed_files_are_current():
    """Run `python -m scripts.build_pipeline_explorer` and commit the result if this fails."""
    assert (build.DOCS / "25-decision-register.md").read_text(encoding="utf-8") == build.render_markdown()
    assert (build.DOCS / "pipeline-explorer.html").read_text(encoding="utf-8") == build.render_document()
