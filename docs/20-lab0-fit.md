# ProcessGhost and Lab0: fit notes

Working notes, not a pitch. Based only on Lab0's public site (lab0.ai): agents for Discovery,
Integration, Testing and Hypercare, aimed at enterprise software implementation. The site mentions
documents, systems and team requirements as inputs. It does not mention screen recordings. Nothing
here is known about Lab0's internals.

Status tags: **[built]** exists and was verified, **[partial]** exists with known limits,
**[planned]** not built yet. Re-check every tag on the day this is shared.

## What ProcessGhost is

When an expert explains a process while doing it on screen, the narration skips the details a new
hire would need: which sheet, which column, which exact status value a formula depends on.
ProcessGhost recovers those details and produces an annotated SOP, with every gap tied to evidence.

## How it works

Four stages, all producing one validated `SOPDocument`:

1. **Writer** turns the Whisper transcript into ordered, timestamped steps. It is deliberately
   naive and never repairs a vague reference. [built]
2. **Reviewer** names what carries between consecutive steps. Where it can't, a deterministic Python
   coverage check flags the omission. It also flags unresolved pointer words, missing names,
   unstated selection rules and unverified "automatic" claims. [partial: flag count varies between
   runs, see docs/18-known-gaps-v2.md]
3. **Checker** takes each flag and checks it against real video frames (OpenAI vision, or one OCR
   pass plus text reasoning with Sarvam). It writes down what it literally observes before giving a
   verdict: resolved, contradicted or unresolvable. [partial: v2 frame path built; an opt-in v3 path
   (`--screen`) works from screen-change events with OCR text first and vision only on escalation,
   built and run once on the one clip at about $0.14 per run with no failed flags, but some "resolved"
   answers are too generic; the Sarvam path has never run against the real API]
4. **Triage** is a pure function of status and severity, with no LLM call. It writes `sop.md` and a
   severity-sorted `build_spec.json` escalation queue. [built]

## Fit with a discovery workflow

The problem: the slowest part of discovery is getting data. Access takes a long time to be granted,
and files that do arrive are either missing things or an information dump. A narrated recording
needs no system access.

ProcessGhost is a feeder, not a replacement. Discovery tools work from documents and conversations.
ProcessGhost adds one more kind of evidence: what was observed on screen. Flags from a walkthrough
become open questionnaire items, each with a frame, timestamp and status.

- Output today is JSON (`sop.json`, `build_spec.json`) plus a readable `sop.md`. [built]
- Questionnaire-item export: [planned]
- Accepting open questions from a discovery tool as Reviewer input (questions only, never answers):
  [planned] (intake design exists in the plan file for the discovery extension; Phase 0-1 done,
  paused).
- Hand-off form: a GitHub repo with docs on architecture decisions, known issues and scope limits.
  No hosted API.

## Why it is built this way

- **The Writer is naive on purpose.** If it repairs "paste it here", the gap disappears. Prior
  context (for example open questions from a discovery tool) goes to the Reviewer only, never the
  Writer. [design settled, intake not built: planned]
- **Two evidence types stay separate.** Every line is tagged said or observed on screen (`[said @t]`
  / `[inferred from screen @t]`), enforced by a raising Pydantic validator. [built] A human
  accepting or rejecting before anything becomes a requirement: [planned]
- **Triage has no LLM.** Whether an issue blocks, warns or passes is deterministic, so results are
  repeatable and auditable. [built]
- **"Unresolvable" is a valid answer.** The Checker must write what it observed first, and is
  allowed to say it can't tell. A confident wrong resolution is worse than an open flag. [built]
- **It needs no system access.** It works from a recording, so it adds value in the weeks while
  access is still being granted. Where access exists later, flags tell an agent where to look.
- **Cost has a ceiling, not a cap that has been exercised.** In v2 the Checker's per-flag vision calls
  dominated (about $0.04 to $0.06 each); the v3 path measured about $0.015 per flag on one run. `MAX_CHECKED_FLAGS=20` limits flags per run, but it has
  never once bound in a real run, so the claim is untested. Every call is logged in `trace.jsonl`.
  [partial]

## Status and limits

- Built and verified on one 24-second scenario (a recording plus a bills-tracker workbook).
- Reviewer flag count varies between runs. Results should be reported as a range over repeated
  runs, not a single number. No harness reports this yet. [planned: eval harness]
- Screen timeline (pixel-diff change events) [built, one clip]. Align's silent-step detection
  [built, found nothing on the one clip, so unproven]. Eval labels for the screen events are drafts,
  not confirmed by a person.
- Idempotent reruns (content-addressed cache, verified: second identical run cost $0 and gave the
  same output), single transient-only retry layer, run manifest, `schema_version` and JSON Schemas on
  the outputs. [built]
- Not yet tested on long calls (20 to 30 minutes) or multiple speakers.
- Logic that is neither spoken nor visible, such as a long SQL query behind a button, is out of
  reach by design. Flags point to it but cannot resolve it.

## Before sharing

- Re-check every status tag against the code on the day it is sent.
- Say only what ProcessGhost adds. Do not make claims about what Lab0's agents lack.
