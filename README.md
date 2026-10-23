# ProcessGhost

When an expert explains a process while doing it on screen, the narration skips the details a new
hire would need: which sheet, which column, which exact status value a formula depends on.
ProcessGhost takes the recording, finds those gaps, and produces a process flow and an annotated SOP
in which every gap is tied to evidence (a transcript quote, a screen change, a cell in the workbook, or an
honest "not shown"). What it cannot settle goes back to the person who recorded it as a short question,
with the evidence already attached.

It needs only a recording. An optional workbook (read-only) lets it answer what the screen cannot. No
system access, no click logs.

It cannot prove nothing is missing (an action off screen, or a rule nobody mentioned that the data does
not hold, never appears), so a flow with no gaps says "no known gaps", never "complete".

## Why this exists

The slowest part of discovery is getting data. Access is granted slowly, and the files people do
send are either missing things or an information dump. A narrated screen recording needs neither:
it can be made today, and it carries two kinds of evidence at once, what was said and what was shown.
ProcessGhost is meant to feed a discovery workflow, not replace one. See `docs/20-lab0-fit.md`.

## How it works

```
video -> [transcribe] -> [Writer] -> [Reviewer] -> [Checker] -> [Triage] -> sop.md, flow.md, build_spec.json
   \                                     ^             ^
    \--> [screen timeline, cell trail] --+-------------+                  (optional, `--screen`)
                                         |
                      [workbook: match check, facts, claim guard]          (optional, `--workbook`)
```

1. **Writer** turns the transcript into ordered, timestamped steps, deliberately naive: a vague
   "paste it here" stays vague.
2. **Reviewer** names what carries between consecutive steps, backed by a verbatim transcript quote
   that code verifies. A pair it cannot bridge becomes a structural flag. It also flags unresolved
   pointer words, missing names, unstated selection rules and unverified "automatic" claims, and
   classifies each step, so a step that moves or edits data must answer four questions: where the value comes
   from, how the right row is picked, where it goes, and what else reads it. A missing answer becomes a question.
3. **Checker** resolves each flag against the screen and writes down what it literally observed
   before giving a verdict: `resolved`, `contradicted` or `unresolvable`.
4. **Triage** is a pure function of status and severity. It decides what blocks, what warns, and
   what proceeds. No model call.
5. **Screen timeline (optional)** finds change events from pixels, then classifies each by what changed in the
   application (never by how many pixels moved), reads which cell was selected from the name box, ties selections to
   speech and later values by time, and turns an unnarrated navigation or data change into a "shown" step.
6. **Workbook (optional)** climbs a ladder for each open question: the video first, then the workbook, then the
   person. The workbook is checked against the recording before it is used (a different version is refused or
   flagged). What code can compute is computed with no model: which formulas read the changed cell, whether their
   conditions are met, which columns match across sheets, how many rows the process covers. A guard withdraws a video
   claim whose number belongs to a cell that does not depend on what was changed. A short, bounded model loop takes only
   what is left. It also names which values are probably parameters (a month typed into a formula) and what would break
   next period, keeping the real names.

Full stage reference with the prompts: `docs/21-pipeline-reference.md`; the architecture: `docs/22-architecture-v3.md`.

## Status, honestly

- Built and verified on **one** 24-second spreadsheet recording and one workbook (`data/`). Not tested on long calls,
  multiple speakers, or web/ERP screens.
- Scored against the findings the scenario requires (`processghost/evals/cases/bills_tracker/`, labels confirmed by the
  owner of the clip): the first real run with the workbook found 3 of 6 at $0.54; after the cell trail, workbook facts,
  claim guard and match check, the second found 9 of 10 at $0.31. The one finding missed (the process repeats for every
  other transaction) is now detected from the workbook, but that and the newer output (results drawn as results,
  parameters and reuse notes) were replayed on the saved run and not re-run with a model.
- The model-driven parts (Reviewer slot questions, Reconciler actions, the workbook loop, the cheap-model read) are
  otherwise unit-tested with fake clients only. 512 tests, none need an API key.
- The Reviewer's flag count varies between runs on identical input (`docs/18-known-gaps-v2.md`), and a video claim can
  still be wrong where no workbook check applies (`docs/23-checker-learnings.md`).
- The workbook open in the recording was not identical to the supplied file; the match check reports it as partial.
- The optional Claude provider has never run against the live API.

Everything above is expanded in `docs/scope-and-limits.md`; the measured results are in `docs/26-adaptive-visual-evidence.md`.

## Quickstart

```
cd processghost
pip install -r requirements.txt -c constraints.txt   # or: conda env create -f environment.yml
export OPENAI_API_KEY=...                # needs ffmpeg on PATH
python -m scripts.run_pipeline ../data/english_hackathon_test.mp4            # v2 behaviour
python -m scripts.run_pipeline ../data/english_hackathon_test.mp4 --screen   # with the screen timeline
python -m scripts.run_pipeline ../data/english_hackathon_test.mp4 --screen \
    --workbook ../data/ProcessGhost_Bills_Tracker_v4_final.xlsx              # with the workbook
python -m evals.score_findings runs/<run_id>                                 # score a run
```

A run writes to `processghost/runs/<run_id>/`: `sop.md` (for people), `flow.md` and `flow.json` (the flow with its
gaps), `sop.json` and `build_spec.json` (for tools, including the questions left for the person), `manifest.json` (what
produced it), `trace.jsonl` (every call, cost and failure), and the evidence frames. With `--workbook` also
`cell_trail.json`, `workbook_profile.json` and `workbook_match.json`.

Running the same input again is served from a local cache and costs nothing; pass `--no-cache` to
force fresh calls. Recordings over 15 minutes or 500 MB are refused, and a run stops starting new
Checker calls once estimated spend passes $2 (`--max-cost` to change). Recordings may contain client
data: read `docs/data-handling.md` first, and use `--no-keep-media` and `python -m scripts.cleanup`. Tests: `cd processghost && python -m pytest tests -q` (no API key needed, except
one integration test that skips without a key and fixture).

## Output contract

`processghost/schemas/` holds JSON Schemas for `sop.json` and `screen_events.json`, and
`build_spec.md` describes `build_spec.json`. Every output carries a `schema_version`. A flag records
where it came from (`origin`), which screen events back it (`event_ids`), which evidence path
resolved it (`checker_tier`) and what the video and the workbook each did with it (`rungs`), so a consumer can tell an
observed answer from an assumed one.

## Where to read next

| If you want | Read |
|---|---|
| Every stage, decision and open question in one place | `docs/25-decision-register.md`, or open `docs/pipeline-explorer.html` in a browser |
| The reasoning behind the main choices | `docs/adr/` (0014 is the flow, slots and evidence ladder) |
| What it does not do | `docs/scope-and-limits.md` |
| What is stored and what leaves the machine | `docs/data-handling.md` |
| How a run works, stage by stage, with prompts | `docs/21-pipeline-reference.md` |
| The architecture (screen timeline, slots, ladder, workbook) | `docs/22-architecture-v3.md` |
| The design and measured results of the adaptive evidence and flow work | `docs/26-adaptive-visual-evidence.md` |
| What the Checker really did on real runs | `docs/23-checker-learnings.md` |
| Known defects | `docs/18-known-gaps-v2.md` |
| How it could feed a discovery workflow | `docs/20-lab0-fit.md` |
