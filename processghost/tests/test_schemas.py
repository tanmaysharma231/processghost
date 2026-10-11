"""Fails when a committed output schema no longer matches its Pydantic model -- run
`python -m scripts.export_schemas` and commit the result."""
from __future__ import annotations

import json

from core.models.screen import ScreenTimeline
from core.models.sop import SOPDocument
from scripts.export_schemas import SCHEMAS_DIR


def _committed(name):
    return json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))


def test_sop_schema_is_current():
    assert _committed("sop.schema.json") == SOPDocument.model_json_schema()


def test_screen_events_schema_is_current():
    assert _committed("screen_events.schema.json") == ScreenTimeline.model_json_schema()
