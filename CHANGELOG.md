# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-07-01

### Added

- Monitor-Coverage-Card (`monitor-coverage-card.v0`) and a sibling `monitor-coverage-card` console script. Where the Evidence-Sufficiency-Card asks whether a scorer/monitor identity is *present* (F07), this card asks whether an identified monitor actually *scored the assistant turns that executed the risky tools*. Over the run's assistant turns, coverage = (risky tool turns carrying a suspicion score from an identified monitor) / (risky tool turns); the card reports `monitor_identity`, `coverage_ratio`, `uncovered_spans`, and a `covered` / `partial-coverage` / `uncovered` verdict.
- `--gate` (and its alias `--require-covered`) on the coverage CLI: exit `1` unless the verdict is `covered`. `--risky-tools transfer,delete,...` names the team-owned risky set (default: every tool-bearing turn). The card prints to stdout, the `gate:` line to stderr, and the gate shares the exit-code contract with the evidence-sufficiency gate (`0` pass / reporting, `1` block, `2` tool/usage error) — now factored into a shared `gatelib` module.
- Core "monitor-coverage trace" input shape (stdlib): a JSON/JSONL list of messages, or `{"monitor", "messages"}`, where the per-turn `suspicion_score` is co-located on the same `assistant` message as its `tool_calls`. A monitor scores the assistant turn, not each call.
- Optional `[inspect]` reader (`inspect_coverage`, imported only when used) that maps a real `.eval` log's `ChatMessageAssistant` messages (`tool_calls` + `metadata.suspicion_score` + monitor identity from `sample.scores` / `eval.metadata`) into the core shape via `inspect_ai.log.read_eval_log`; `monitor-coverage-card path/to/log.eval` uses it when the extra is installed. The per-turn scores are stripped by the ControlArena trajectory export, so the reader goes to the `.eval` log directly.
- Monitor-coverage example (`examples/monitor-coverage/`): a covered trace that passes the gate and an uncovered one (a `transfer` / `delete_record` turn with no suspicion score) that blocks it.

### Notes

- Coverage is not accuracy: the card checks whether an identified monitor *observed* (scored) the risky tool turn, not whether its judgment was correct. It is not a claim that ControlArena or any monitor is broken — a normal run scores every turn and cards as `covered`; the gap appears under partial / sampled / post-hoc monitoring or when a trace names a monitor but carries no per-turn scores. A `covered` verdict is a coverage/presence check, not a safety, quality, correctness, or compliance claim, and does not approve a release.

## [0.2.0] - 2026-07-01

### Added

- Deployment-gate exit-code contract: the CLI can act as a CI gate via its exit code — `--gate` (block on `insufficient`), `--gate --strict` (also block on `conditional`), and `--require-present F06,F07,...` (block if any listed evidence field is not `present`). Exit `2` is reserved for tool/usage errors and is always distinguishable from a gate block (exit `1`).
- Composite GitHub Action (`action.yml`) that installs the package, scores a trace, uploads the card JSON as a workflow artifact, and fails the step when the gate blocks.
- Release-gate example (`examples/release-gate/`): a before/after walkthrough with a raw trace that fails the gate and an eval-instrumented trace that passes.

### Changed

- F09 (transcript and replay handle) reaches `present` only on a structured, machine-identifiable replay handle (a replay/trace id, digest, or uuid), never on a free-text substring. F10 (policy and counterfactual replay notes) is capped at `partial`, mirroring the F11 honest floor. A fabricated free-text replay/counterfactual note can no longer certify a trace or clear all blocking gaps. The card now states, in its claim boundary, that F09/F10 reflect declared replay/counterfactual claims at face value and are not verified.
- The `gate:` verdict line now prints to stderr, so `--format json` yields a clean, parseable card on stdout (pipeable into `jq`); the exit code remains the machine-readable gate signal.

### Fixed

- Deeply-nested / adversarial trace input is bounded (iterative traversal plus a `MAX_TRACE_DEPTH` limit) and mapped to a usage error (exit `2`) instead of escaping as a `RecursionError` traceback (previously exit `1`, indistinguishable from a gate block).
- A malformed pre-generated card (declares the card schema but is missing required top-level keys) is rejected as a usage error (exit `2`) instead of crashing summarization with an uncaught `KeyError`. Passthrough trusts a schema-declaring card's own verdict without recomputation; this is now documented.

## [0.1.0] - 2026-06-30

Initial public release.

### Added

- Evidence-Sufficiency-Card builder over agent eval traces: twelve evidence fields scored present / partial / missing into a conservative verdict.
- stdlib-first CLI (`evidence-sufficiency-card`) for JSON / JSONL traces, generated cards, and ControlArena export directories.
- Optional Inspect scorer (`inspect_adapter.evidence_sufficiency`) that maps a real `TaskState` (model, tools, tool_choice, scores, sample_id/uuid, messages, output) into a card, exercised end-to-end in the CI `inspect` lane.
- ControlArena trajectory-export reader (`trajectory.jsonl` / `tools.json` / `metadata.json`) with a synthetic fixture and test.
- Demos across the evidence spectrum: synthetic fixture, a bundled real Inspect log (mockllm), and pinned + checksum-verified public datasets (Trace Commons, Assayo).
- Deterministic cards: `created_utc` is injectable via `now=` or `SOURCE_DATE_EPOCH` for byte-reproducible output.
- Apache-2.0 license, `CITATION.cff`, typed package (`py.typed`), and CI (lint + offline matrix + Inspect e2e lane).

### Notes

- The card reports **evidence-bound** sufficiency, not a statistically validated metric, and never claims model/benchmark/control-protocol safety or audit, compliance, or legal sufficiency. A controlled validation matrix is future work.
