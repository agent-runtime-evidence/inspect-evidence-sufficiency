# Draft Inspect community proposal

Draft text for an Inspect issue / discussion. It states the gap and the upstream question only; the artifact — its card fields, scorer API, demos, and claim boundary — lives in the repository [`README.md`](../README.md) and is not restated here.

## Problem

Inspect eval logs can preserve the run substrate — task configuration, samples, messages, events, scores, retries/errors, and model API details, depending on log settings — which is valuable for debugging and review. This proposal treats Inspect logs as that substrate and does **not** claim Inspect lacks logs.

The gap I want to explore is **decision sufficiency**: when an eval result is used to support a release or canary decision, can the log show which evidence is present, partial, or missing *for that decision*? A green result whose trace lacks a resolved model snapshot, a scorer manifest, or a replay handle should not silently read as release-sufficient.

## What I built

`inspect-evidence-sufficiency` — an external, stdlib-first package that turns a trace (Inspect log, ControlArena export, or generic JSON/JSONL) into a conservative Evidence-Sufficiency-Card, with an optional Inspect scorer. The verdict tracks how much evidence the input actually carries:

| Input                                          | Verdict        |
| ---------------------------------------------- | -------------- |
| A real Inspect `.eval` log (mockllm)           | `conditional`  |
| A judged public trajectory (Assayo, CC-BY-4.0) | `insufficient` |
| A raw public coding trace                      | `insufficient` |

See the [`README.md`](../README.md) for the twelve card fields, the scorer API, the demos, and the full claim boundary.

## Ask

Where, if anywhere, should this live?

- an external `inspect-*` ecosystem package;
- a docs example for post-hoc log scoring / release-evidence review;
- an extension pattern for custom scorers;
- or out of scope for Inspect?

It is not an upstream feature, a benchmark, or an audit/compliance standard, and not a replacement for benchmark-integrity or monitorability work. If there is interest, I can shape the package around the preferred boundary before opening any PR.
