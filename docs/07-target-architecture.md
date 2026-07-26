# 07 - Target Architecture (Python rebuild)

This document describes the system we are going to build. It is a design, not a description of
anything that exists yet -- no claim here should be read as a statement about the current
repository. As-is facts live in Part 1 (`01`-`05`) and the gap register (`06`); this document
assumes both have been read.

## Core principle: server-side, durable orchestration

The single biggest structural problem in the as-is system (`06-known-gaps.md` entry 8) is that a
pipeline run is nothing but transient React state. The target architecture inverts this: **a
pipeline run is a durable, inspectable record**, and the UI (whatever it ends up being, see
`11-open-questions.md`) is a viewer/controller over that record, not the thing that holds it.

Concretely: a run must survive the client disconnecting, must be resumable, must be queryable after
the fact, and every agent call within it must be individually inspectable -- prompt, raw response,
parsed output, timing, token usage. This is the Debug Panel's event log (`05-frontend-behavior.md`
section 4) promoted from a nice-to-have UI feature to the system's actual audit trail and source of
truth.

## Proposed package layout

```
processghost/
  pipeline/
    stages.py          # one function/class per pipeline stage (transcribe, extract, detect_gaps,
                        # fill_visual_gaps, synthesize, judge)
    orchestrator.py     # runs stages in order, owns the judge loop, persists StageExecutions
  agents/
    client.py           # thin wrapper over whichever provider(s) are chosen (see decision below)
    roles.py             # per-agent role definition: system prompt template, model, temperature,
                          # output model -- the Python analogue of workflow_state.json
  media/
    frames.py            # ffmpeg-based frame extraction at timestamp windows, crop application
    audio.py              # audio extraction from video for transcription input
  models/
    transcript.py, steps.py, gaps.py, visual_fills.py, sop.py, judge.py
                          # pydantic models mirroring response_schemas/*.json, see 10-migration-map.md
  store/
    runs.py               # Run / StageExecution persistence (see "Run model" below)
  api/
    (thin HTTP surface, exact shape deferred -- see 11-open-questions.md)
  prompts/
    *.md or *.jinja        # versioned prompt templates, not string literals in application code
```

This mirrors the as-is split reasonably closely (agents, pipeline orchestration, data contracts,
media handling as a new first-class concern, and a persistence layer that did not exist before) so
that `10-migration-map.md` can map old files to new modules directly.

## Run model

A `Run` is the unit of work: one uploaded walkthrough, from upload through final SOP (or failure).

```
Run
  id
  created_at
  input_ref            # pointer to the stored source video/audio (and any accompanying file, e.g.
                        # the spreadsheet in the demo scenario)
  status                # queued | running | awaiting_review | complete | failed
  tone
  max_passes
  pass_count
  stages: list[StageExecution]
  final_output: SopDocument | None
```

```
StageExecution
  run_id
  stage_name           # transcribe | extract | detect_gaps | fill_visual_gaps | synthesize | judge
  pass_number           # which judge-loop iteration this execution belongs to
  started_at, finished_at
  prompt                # the exact prompt/messages sent
  raw_response           # unmodified provider response
  parsed_output           # validated pydantic model, or a typed ParseError
  token_usage
  status                 # ok | error
  error                   # if status == error
```

This directly replaces `DebugLogEntry` (`app/sections/DebugPanel.tsx:7-15`) as a concept, but
persisted server-side rather than held only in a React array, and typed rather than a loosely
optional-payload string.

## Provider decision

Stated explicitly as a decision, not assumed:

**Option A -- keep calling Lyzr-hosted agents by `agent_id`.** Preserves the six already-tuned
agents and their IDs from `workflow_state.json` without re-authoring their system prompts. Keeps
`LYZR_API_KEY` as the only credential. Downsides: still routes through Lyzr's async task/poll model
(`03-agent-pipeline.md` section on lifecycle isn't ours to change), and does not resolve gap
register entry 7 -- Lyzr's hosted agents are still whatever black box produced the four-layer
unwrap problem in the first place.

**Option B -- call Anthropic / Google / OpenAI SDKs directly**, one per the model already assigned
to each role in `workflow_state.json` (Transcription: Gemini, Extraction/Gap Detection/Judge:
Claude, Visual Gap Filler: Gemini, Synthesis: GPT). Requires re-authoring each agent's system
prompt as something we own and version (see `prompts/` above), and requires three provider API
keys instead of one. Gains: enforced structured output per provider (JSON schema / tool-use mode),
direct control over retries and timeouts, no dependency on Lyzr's task queue semantics, and a
straight fix for gap register entry 7.

**Recommendation: Option B.** The four-layer unwrap stack (`04-data-contracts.md` section 2) exists
specifically because nothing in the current chain enforces the declared schema; owning the prompt
and using provider-native structured output removes that problem at the source rather than adding
a fifth layer of guessing. This also removes a general-purpose black-box dependency (Lyzr) in favor
of the specific model providers already named in `workflow_state.json`, which is more inspectable
and directly debuggable when a call misbehaves.

**Note for implementation:** before writing any Anthropic-facing code under this decision, load the
`claude-api` skill for current model IDs, structured-output mechanics, and pricing -- do not
hand-write Claude API integration from memory.

## Parsing and validation

Every agent call target has a pydantic model (see `10-migration-map.md` for the field-by-field
mapping from each `response_schemas/*.json`). A stage's `parsed_output` is either a valid instance
of that model or a typed `ParseError` recorded on the `StageExecution` -- never a silently
reshaped, best-guess dict. This replaces `parseLLMJson` + `normalizeResponse` + `parseAgentResult` +
`extractText` as a set (`04-data-contracts.md` section 2) with one explicit contract per stage.

## What this does not decide yet

Whether runs persist to sqlite, flat JSON files, or Postgres; whether execution is synchronous or
queued; and what the UI layer looks like are all left to `11-open-questions.md` -- they are
implementation choices within this architecture, not architectural decisions this document needs to
settle before pipeline work (`08`, `09`) can be designed.
