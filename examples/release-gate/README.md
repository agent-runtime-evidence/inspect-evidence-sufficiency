# Release-gate demo

This demo shows the CLI acting as a CI deployment gate: the exit code, not the printed card, carries the block/pass signal. The two traces differ in one decisive way — whether the run recorded a scorer/monitor identity (field F07). A green-looking run can still be missing the evidence that a monitor was ever there.

The card (summary or JSON) prints to **stdout**; the `gate:` verdict line prints to **stderr**. The Observed blocks below show the stdout card followed by the stderr `gate:` line; in a shell they arrive on separate streams, so `--format json` on stdout stays clean and pipeable.

- `trace-missing-monitor.json` — a raw agent session with a prompt, one tool call, and an answer, but no scorer or monitor identity. F07 (scorer and monitor identity) is `missing`.
- `trace-with-monitor.json` — the same run, properly eval-instrumented: it adds a resolved model digest, a scorer manifest, an explicit monitor, and recorded scores. F07 is `present`.

Both traces are synthetic fixtures in the tool's real JSON trace format. They exercise the gate; they are not real eval results and make no claim about any model or system.

## Blocked: the monitor was never recorded

You choose which fields a release requires. Here the release requires that a scorer/monitor identity (F07) be recorded — so a run that never logged one is blocked, whatever its aggregate score:

```bash
PYTHONPATH=src python3 -m inspect_evidence_sufficiency \
  examples/release-gate/trace-missing-monitor.json \
  --release-decision "Can the billing agent enter a limited internal canary?" \
  --eval-objective "Confirm answers are grounded in the approved knowledge base and no unauthorized tool was used." \
  --format summary --gate --require-present F07
```

Observed (stdout card, then the `gate:` line on stderr):

```
verdict: conditional
sufficiency_score: 0.542
status_counts: {'present': 4, 'partial': 5, 'missing': 3}
blocking_gaps:
- F03 model/provider/resolved version binding: partial
- F07 scorer and monitor identity: missing
- F09 transcript and replay handle: partial
- F10 policy and counterfactual replay notes: missing
gate: BLOCK (exit 1) | required field(s) not present: F07=missing
```

`echo $?` prints `1`. The block stands even though the aggregate verdict is only `conditional`, not a hard `insufficient` — that a monitor was recorded is a field-level requirement you set, not something the overall score decides for you. The listed blocking gaps (F03/F07/F09/F10) are the fields this raw trace does not fully evidence; here the `--require-present F07` requirement is what sets the exit code.

## Passing: the instrumented trace clears the gate

The eval-instrumented trace records the scorer manifest and the monitor, so F07 is present and the same command passes:

```bash
PYTHONPATH=src python3 -m inspect_evidence_sufficiency \
  examples/release-gate/trace-with-monitor.json \
  --release-decision "Can the billing agent enter a limited internal canary?" \
  --eval-objective "Confirm answers are grounded in the approved knowledge base and no unauthorized tool was used." \
  --format summary --gate --require-present F07
```

Observed (stdout card, then the `gate:` line on stderr):

```
verdict: conditional
sufficiency_score: 0.667
status_counts: {'present': 6, 'partial': 4, 'missing': 2}
blocking_gaps:
- F09 transcript and replay handle: partial
- F10 policy and counterfactual replay notes: missing
gate: PASS (exit 0) | verdict 'conditional'
```

`echo $?` prints `0`. F07 is present, so the `--require-present F07` requirement is met and the gate passes — even though F09/F10 remain non-blocking gaps at the aggregate level (this gate keys on F07, not on them).

## Gating on the overall verdict too

Beyond specific fields, `--gate` blocks whenever the overall verdict is `insufficient`, and `--gate --strict` also blocks a `conditional` verdict (only `sufficient-for-named-decision` passes). Scored on its own with no release decision to anchor it, the missing-monitor trace is `insufficient`, so a plain `--gate` blocks it:

```bash
PYTHONPATH=src python3 -m inspect_evidence_sufficiency \
  examples/release-gate/trace-missing-monitor.json \
  --format summary --gate
```

This prints `gate: BLOCK (exit 1) | verdict 'insufficient' fails --gate` on stderr and exits `1`.

## Reporting mode never blocks

Without `--gate` and without `--require-present`, the CLI only reports; it always exits `0`, even on an `insufficient` trace:

```bash
PYTHONPATH=src python3 -m inspect_evidence_sufficiency \
  examples/release-gate/trace-missing-monitor.json --format summary
```

This prints `gate: report-only (exit 0) | verdict: insufficient` on stderr and exits `0`. Adopt the gate incrementally: start in reporting mode, then turn on `--require-present` and/or `--gate` once teams are ready for it to block.

## Exit codes

| exit | meaning                                                                                                                          |
| ---- | -------------------------------------------------------------------------------------------------------------------------------- |
| 0    | pass, or reporting mode (no gate requested)                                                                                      |
| 1    | gate block: the requested gate condition was not met                                                                             |
| 2    | tool/usage error: unparseable or excessively nested trace, a malformed pre-generated card, bad arguments, or an unknown field id |

Exit `2` is always distinguishable from a gate block (exit `1`), so a CI job can tell "the gate said no" apart from "the tool could not run". A `.json` input that already declares the card schema is trusted and passed through as-is (its own verdict is used without recomputation); a card that claims the schema but is missing required top-level keys is a usage error, not a gate block.

## Boundary

This is a presence gate. It blocks when required evidence is missing or the evidence is insufficient to support a named decision. It is not a safety, quality, or compliance claim, and a pass does not approve a release — it only reports that the evidence a trace carries clears the configured presence bar.
