# Inspect Evidence Sufficiency

**Turn an agent eval trace into an Evidence-Sufficiency-Card — a reviewer-facing report of what evidence is present, partial, or missing before an eval result is used as release evidence.**

Agent eval results are routinely used to justify a ship / canary / gate decision. But the trace behind a green result often lacks the evidence needed to support that claim: a resolved model snapshot, the scorer manifest, the tool and permission surface, a replay handle. This package reads an existing trace — an Inspect log, a ControlArena export, or generic JSON/JSONL — and produces a conservative card that names exactly which evidence is there and which release claims remain unsupported, so a "pass" is not mistaken for "sufficient".

It is deliberately **evidence-bound**: it reads existing traces and logs, runs no new models, fabricates no outcomes, and never claims that Inspect or ControlArena lack logs. It is a research/proposal package — not an upstream Inspect contribution yet, not a benchmark result, and not a compliance/audit opinion.

## What you get

- a stdlib **CLI** that scores a trace into a card (twelve evidence fields → a verdict);
- an optional **Inspect scorer** that emits the card from a `TaskState`;
- a **ControlArena** export reader for post-hoc cards;
- a **reconstructability probe**: whether the observed run can be reconstructed, and what counterfactual replay remains unproven.

## Where this fits

Agent eval results are increasingly used as *release evidence*, but a passing score can hide whether the evidence behind it supports the decision — the "outcome fallacy" / "evidence-to-action gap" now widely documented in agent-eval research. This package sits one layer above the trace and is intentionally narrow:

- **Eval frameworks and observability** — Inspect and ControlArena, or platforms like Langfuse / Arize Phoenix / Galileo / AgentOps — *produce and store* the trace. This package *reads* their output; it is not another logger or platform.
- **Failure attribution** — failure tracers that pinpoint *which agent or step failed* — answer a different question than this one: *is there enough evidence to support the claim the result is being used for?*
- **Evidence tracing / execution provenance** — represents the provenance graph; the gap is that there is no runnable deployment-readiness / sufficiency scorer yet. This is a small, open step toward one: a vendor-neutral, evidence-bound card naming present / partial / missing evidence and a conservative verdict.

It is deliberately a thin wedge, not a governance or "trust" platform: it scores the sufficiency of evidence a trace already contains, and stops there. Method and full related work will accompany a forthcoming preprint.

## Quick Start

```bash
git clone <repo-url> inspect-evidence-sufficiency
cd inspect-evidence-sufficiency
make verify          # offline: tests + synthetic + a real Inspect log
make verify-public   # also fetches and scores one public Trace Commons trace
```

`make verify` is fully offline and needs no third-party dependencies: it runs the stdlib unit tests, scores a bundled synthetic Inspect-style fixture, and scores a bundled **real Inspect eval log** (`examples/inspect-log-mockllm.json`, produced by `mockllm/model`, no API key). `make verify-public` adds the network demo that fetches one small public Trace Commons trace.

Cards are deterministic given fixed inputs and a fixed timestamp: the Makefile pins `SOURCE_DATE_EPOCH`, so re-running a demo reproduces the same card byte for byte. Outputs are written under `out/` (git-ignored):

- `out/synthetic-card.json`
- `out/inspect-log-card.json`
- `out/trace-commons-card.json` (only via `verify-public`)

## CLI

```bash
PYTHONPATH=src python3 -m inspect_evidence_sufficiency path/to/log.jsonl \
  --source-url "https://huggingface.co/datasets/trace-commons/agent-traces" \
  --release-decision "Can this trace support a release-evidence claim?" \
  --eval-objective "Probe evidence present/partial/missing fields." \
  --output out/card.json
```

For a ControlArena export directory containing `trajectory.jsonl`, `tools.json`, and `metadata.json`:

```bash
PYTHONPATH=src python3 -m inspect_evidence_sufficiency path/to/controlarena-export \
  --release-decision "..." \
  --eval-objective "..." \
  --output out/controlarena-card.json
```

## Deployment Gate

The same CLI can act as a CI deployment gate: it prints the card as before, and its **exit code** carries the block/pass signal so a workflow can stop a ship when the evidence is not there. Reporting stays the default — without `--gate` the CLI always exits `0`, so adding it to a pipeline changes nothing until you opt in.

- `--gate` — exit `1` when the verdict is `insufficient` (blocks the deployment); `conditional` and `sufficient-for-named-decision` pass.
- `--gate --strict` — also exit `1` on `conditional`; only `sufficient-for-named-decision` passes.
- `--require-present F06,F07,...` — exit `1` if any listed evidence field is not `present`, in any mode. Field ids are validated against the card's real F01–F12 set; an unknown id is a usage error (exit `2`).

| mode                  | insufficient | conditional | sufficient-for-named-decision |
| --------------------- | ------------ | ----------- | ----------------------------- |
| default (no `--gate`) | 0            | 0           | 0                             |
| `--gate`              | 1            | 0           | 0                             |
| `--gate --strict`     | 1            | 1           | 0                             |

Exit `2` is reserved for tool/usage errors — an unparseable trace, bad arguments, or an unknown field id — and is always distinguishable from a gate block (exit `1`), so a CI job can tell "the gate said no" apart from "the tool could not run".

```bash
# Block a deploy unless the trace clears the presence bar and records a monitor.
PYTHONPATH=src python3 -m inspect_evidence_sufficiency path/to/log.json \
  --release-decision "Can this agent build enter a limited internal canary?" \
  --eval-objective "Confirm answers are grounded and no unauthorized tool was used." \
  --gate --require-present F07
echo $?   # 0 = pass, 1 = blocked, 2 = tool/usage error
```

A runnable before/after walkthrough (a raw trace that fails the gate, and the eval-instrumented version that passes) is in [`examples/release-gate/`](examples/release-gate/).

### GitHub Action

A composite action at the repo root wraps the gate for CI. It installs the package, scores the trace, uploads the card JSON as an artifact, and fails the step when the gate blocks:

```yaml
- uses: agent-runtime-evidence/inspect-evidence-sufficiency@v0.2.0
  with:
    trace: path/to/eval-log.json
    release-decision: "Can this agent build enter a limited internal canary?"
    eval-objective: "Confirm answers are grounded and no unauthorized tool was used."
    mode: gate            # gate | strict | report (default: gate)
    require-present: F07  # optional; comma-separated evidence-field ids
```

Inputs: `trace` (required), `release-decision` (required), `eval-objective`, `mode` (default `gate`), `require-present`, `output` (card path, default `evidence-sufficiency-card.json`), `upload-card` (default `true`). The step's success/failure mirrors the CLI exit code, so a blocked gate fails the job.

### Gate boundary

This is a **presence gate**: it blocks when required evidence is missing or the evidence a trace carries is insufficient to support a named decision. It is **not** a safety, quality, or compliance claim, and a pass does **not** approve a release. A pass means only that the evidence present in the trace clears the configured presence bar; the release decision itself remains a human one, subject to normal review.

## Optional Inspect Scorer

The package is stdlib-first. If `inspect-ai` is installed, the optional scorer can be used from `inspect_evidence_sufficiency.inspect_adapter`:

```python
from inspect_evidence_sufficiency.inspect_adapter import evidence_sufficiency

scorer = evidence_sufficiency(
    release_decision="Can this eval support the release gate?",
    eval_objective="Check evidence sufficiency for the observed eval result.",
)
```

The scorer returns a numeric sufficiency ratio and stores the full card in `Score.metadata["evidence_sufficiency_card"]`.

## Evidence Fields

The card uses twelve fields:

1. eval objective and release decision
1. task and dataset sample scope
1. model/provider/resolved version binding
1. prompt, scaffold, and agent policy identity
1. tools, sandbox, and network permissions
1. raw messages, events, and tool calls
1. scorer and monitor identity
1. retries, errors, and timeouts
1. transcript and replay handle
1. policy and counterfactual replay notes
1. leakage and reward-hacking checks
1. residual uncertainty and not-sufficient-for statement

Present = 1.0, partial = 0.5, missing = 0.0. The overall verdict is conservative: missing replay, scorer identity, model binding, or scope evidence keeps the card conditional or insufficient.

## Public Trace Demo

The public demo uses Trace Commons (`trace-commons/agent-traces`) because it is a public CC-BY-4.0 dataset of donated coding-agent sessions with prompts, model responses, tool calls, command output, and explicit anonymization caveats. The trace content is fetched at runtime and ignored by git.

The demo intentionally produces a limited card: public traces often contain messages and tool calls but not release decisions, scorer manifests, resolved model snapshots, or counterfactual replay results. That limitation is the artifact's point.

## Datasets and Validation

Open inputs the package runs on, by evidence richness (highest to lowest):

- **Real Inspect eval log** — `examples/inspect-log-mockllm.json` is a genuine Inspect `.eval` log in JSON format, generated by `mockllm/model` (no API key, no network). It is the primary "real Inspect log" input and is scored offline by `make demo-inspect-log`. The CI `inspect` lane additionally regenerates a fresh log each run. The same path works on any real Inspect log you produce, including public [Inspect Evals](https://github.com/UKGovernmentBEIS/inspect_evals) benchmark logs.
- **ControlArena trajectory exports** — read directly from a `trajectory.jsonl` / `tools.json` / `metadata.json` export directory.
- **Assayo judged trajectories** — [`Assayo/web-research-trajectories`](https://huggingface.co/datasets/Assayo/web-research-trajectories) (CC-BY-4.0), a public set of web-research agent trajectories with tools, steps, a generator model, and a rubric verdict. One judged trajectory is fetched and scored by `make demo-assayo`.
- **Trace Commons public traces** — [`trace-commons/agent-traces`](https://huggingface.co/datasets/trace-commons/agent-traces) (CC-BY-4.0), donated coding-agent sessions, scored by `make demo-public`.

Public inputs are **pinned to a dataset commit revision and verified by sha256** (`examples/source_manifest.json`); fetched contents are git-ignored, never vendored.

What this shows is **evidence-bound behavior, not a validated metric**: the score tracks how much evidence an input actually carries. Across real public inputs the verdicts form a spectrum that matches each input's evidence — a real instrumented Inspect log cards `conditional` (dataset scope, scorer identity, transcript), a judged Assayo trajectory cards `insufficient` (tools and a rubric verdict, but no eval-run scaffolding or resolved model), and a raw coding trace cards `insufficient` (messages only). A statistical validation of the score (a controlled matrix of models × scaffolds × benchmarks with measured reconstruction success) is deliberately out of scope for this artifact and is tracked as future work, not claimed here.

## Source Anchors

- Inspect AI docs: task/scorer model, custom scorers, log API, and `read_eval_log`.
- ControlArena README: trajectory export produces `trajectory.jsonl`, `tools.json`, and `metadata.json` for post-hoc analysis.
- Trace Commons dataset card: public agent traces, CC-BY-4.0 compilation license, and privacy/licensing caveats.

## Claim Boundary

Allowed:

- "This card shows which evidence an eval trace contains and which release-evidence claims remain unsupported."
- "The package can score Inspect-style logs, ControlArena trajectory exports, and generic agent trace JSON/JSONL."

Avoid:

- "Inspect/ControlArena lack logs."
- "This is accepted upstream by Inspect or ControlArena."
- "This proves a model, benchmark, or control protocol is safe."
- "This is audit/compliance/legal sufficiency."

## Development

```bash
pip install -e ".[dev,inspect]"
make verify   # offline tests + demos
make lint     # ruff + mdformat checks
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the design constraints (stdlib-first, evidence-bound, no overclaiming, deterministic, no vendored traces) and [`CHANGELOG.md`](CHANGELOG.md) for release notes.

## License & Citation

Licensed under the Apache License 2.0 — see [`LICENSE`](LICENSE).

If you use this in research, cite the software artifact via [`CITATION.cff`](CITATION.cff) (GitHub renders a "Cite this repository" entry; Zenodo reads the same file). A concept DOI (which always resolves to the latest release) and per-version snapshot DOIs are recorded in `CITATION.cff`; the Zenodo–GitHub integration mints a fresh snapshot DOI on each tagged release.
