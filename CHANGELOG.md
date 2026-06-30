# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
