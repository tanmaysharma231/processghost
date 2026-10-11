"""Write JSON Schema files for the output contracts other tools consume.

    python -m scripts.export_schemas

Writes schemas/sop.schema.json (sop.json), schemas/screen_events.schema.json (screen_events.json) and
schemas/build_spec.md (build_spec.json is assembled by report/build_spec.py as a plain dict, so it is
described rather than schema-generated). Re-run after changing a model; tests/test_schemas.py fails if
the committed files are stale.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.models.screen import ScreenTimeline
from core.models.sop import SCHEMA_VERSION, SOPDocument

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"

BUILD_SPEC_DOC = f"""# build_spec.json (schema_version {SCHEMA_VERSION})

Produced by `report/build_spec.py: build_spec`, a pure function of the validated `SOPDocument`
(`sop.schema.json`). Same document in, same output out.

| Key | Type | Meaning |
|---|---|---|
| `schema_version` | string | Version of this contract and of `sop.json` |
| `run_id` | string | The run that produced it |
| `steps` | list | The SOP steps in order, as in `sop.json` |
| `flags` | list | Every flag with its `triage` verdict (see below) |
| `escalation_queue` | list | The flags whose triage is `block_and_escalate`, severity-sorted |
| `provenance_complete` | bool | Every step has a `said`/`inferred` attribution and every flag a window |
| `review_failed` | bool | The Reviewer stage failed and the Writer's draft shipped unreviewed |
| `timing_degraded` | bool | Speech-to-text returned no per-line timing; timestamps are unreliable |

Each entry in `flags` has: `flag_id`, `family` (`ambiguity` or `structural`), `question`, `severity`
(`blocking` / `caution` / `note`), `status` (`open`, `resolved`, `contradicted`, `unresolvable`,
`unchecked`, `check_failed`), `step_id`, `between_step_ids`, `window_segment_ids`,
`window_time_range`, `origin` (`reviewer` / `coverage` / `screen`), `event_ids`, `checker_tier`
(`ocr` / `vision` / `fallback_frames` / null) and `triage`.

`triage` is a pure function of `status` and `severity`:
`resolved` -> `proceed_with_assumption`; `contradicted` -> `block_and_escalate`; any other status with
severity `blocking` -> `block_and_escalate`; otherwise `proceed_with_flag`.
"""


def main() -> None:
    SCHEMAS_DIR.mkdir(exist_ok=True)
    for name, model in (("sop.schema.json", SOPDocument), ("screen_events.schema.json", ScreenTimeline)):
        (SCHEMAS_DIR / name).write_text(json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8")
        print("wrote", SCHEMAS_DIR / name)
    (SCHEMAS_DIR / "build_spec.md").write_text(BUILD_SPEC_DOC, encoding="utf-8")
    print("wrote", SCHEMAS_DIR / "build_spec.md")


if __name__ == "__main__":
    main()
