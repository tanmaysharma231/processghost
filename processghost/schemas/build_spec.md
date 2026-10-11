# build_spec.json (schema_version 3.0)

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
