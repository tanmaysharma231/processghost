# ProcessGhost

When an expert explains a process while doing it on screen, the narration skips the details a new
hire would need: which sheet, which column, which exact status value a formula depends on.
ProcessGhost takes the recording, finds those gaps, and produces an annotated SOP in which every gap
is tied to evidence (a transcript quote, a screen frame, or an honest "not on screen").

It needs only a recording. No system access, no click logs.

## Why this exists

The slowest part of discovery is getting data. Access is granted slowly, and the files people do
send are either missing things or an information dump. A narrated screen recording needs neither:
it can be made today, and it carries two kinds of evidence at once, what was said and what was shown.
ProcessGhost is meant to feed a discovery workflow, not replace one. See `docs/20-lab0-fit.md`.

## How it works

```
video -> [transcribe] -> [Writer] -> [Reviewer] -> [Checker] -> [Triage] -> sop.md, build_spec.json
   \                                     ^             ^
    \--> [screen timeline] -> [Align] ---+-------------+        (optional, `--screen`)
```

1. **Writer** turns the transcript into ordered, timestamped steps, deliberately naive: a vague
   "paste it here" stays vague.
2. **Reviewer** names what carries between consecutive steps, backed by a verbatim transcript quote
   that code verifies. A pair it cannot bridge becomes a structural flag. It also flags unresolved
   pointer words, missing names, unstated selection rules and unverified "automatic" claims.
3. **Checker** resolves each flag against the screen and writes down what it literally observed
   before giving a verdict: `resolved`, `contradicted` or `unresolvable`.
4. **Triage** is a pure function of status and severity. It decides what blocks, what warns, and
   what proceeds. No model call.
5. **Screen timeline + Align (optional)** find change events from pixels alone, flag changes no step
   describes, and let the Checker work from what changed instead of fixed-interval frames.

Full stage reference with the prompts: `docs/21-pipeline-reference.md`.

## Status, honestly

- Built and verified on **one** 24-second spreadsheet recording (`data/`). Not tested on long calls,
  multiple speakers, or web/ERP screens.
- The Reviewer's flag count varies between runs on identical input (`docs/18-known-gaps-v2.md`).
- The optional screen path cut cost on that clip (about $0.14 per run vs $0.24 to $0.38) and
  removed rate-limit failures, but some "resolved" answers are too generic (`docs/23-checker-learnings.md`).
- Silent-step detection (a step on screen that is never spoken) is built but has not yet found one
  on real data.
- The eval labels in `processghost/evals/` are drafts and unconfirmed.

Everything above is expanded in `docs/scope-and-limits.md`.

## Quickstart

```
cd processghost
pip install -r requirements.txt -c constraints.txt   # or: conda env create -f environment.yml
export OPENAI_API_KEY=...                # needs ffmpeg on PATH
python -m scripts.run_pipeline ../data/english_hackathon_test.mp4            # v2 behaviour
python -m scripts.run_pipeline ../data/english_hackathon_test.mp4 --screen   # with the screen timeline
```

A run writes to `processghost/runs/<run_id>/`: `sop.md` (for people), `sop.json` and
`build_spec.json` (for tools), `manifest.json` (what produced it), `trace.jsonl` (every call, cost
and failure), and the evidence frames.

Running the same input again is served from a local cache and costs nothing; pass `--no-cache` to
force fresh calls. Recordings over 15 minutes or 500 MB are refused, and a run stops starting new
Checker calls once estimated spend passes $2 (`--max-cost` to change). Recordings may contain client
data: read `docs/data-handling.md` first, and use `--no-keep-media` and `python -m scripts.cleanup`. Tests: `cd processghost && python -m pytest tests -q` (no API key needed, except
one integration test that skips without a key and fixture).

## Output contract

`processghost/schemas/` holds JSON Schemas for `sop.json` and `screen_events.json`, and
`build_spec.md` describes `build_spec.json`. Every output carries a `schema_version`. A flag records
where it came from (`origin`), which screen events back it (`event_ids`) and which evidence path
resolved it (`checker_tier`), so a consumer can tell an observed answer from an assumed one.

## Where to read next

| If you want | Read |
|---|---|
| The reasoning behind the main choices | `docs/adr/` |
| What it does not do | `docs/scope-and-limits.md` |
| What is stored and what leaves the machine | `docs/data-handling.md` |
| How a run works, stage by stage, with prompts | `docs/21-pipeline-reference.md` |
| The v3 design (screen timeline, Align) | `docs/22-architecture-v3.md` |
| What the Checker really did on real runs | `docs/23-checker-learnings.md` |
| Known defects | `docs/18-known-gaps-v2.md` |
| How it could feed a discovery workflow | `docs/20-lab0-fit.md` |
