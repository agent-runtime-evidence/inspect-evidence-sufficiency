# Monitor-coverage demo

This demo shows the `monitor-coverage-card` gate. It asks a sharper question than the evidence-sufficiency gate: not *is a monitor identity present*, but *did an identified monitor actually score the turns that executed the risky tools?* Presence is not coverage.

**Coverage is not accuracy.** The card checks whether an identified monitor *observed* (produced a suspicion score for) the assistant turn that ran a risky tool — never whether the monitor's judgment was right. A 99%-catch-rate monitor that was never invoked on the risky turn has zero coverage here; a monitor that scored every turn but scored them all wrong still reads as fully covered. Judging the verdict is a separate, out-of-scope question.

Per-step monitor scores live on the assistant message, co-located with the tool calls it is scoring: `metadata.suspicion_score` (a number 0–1) on the same `assistant` message that carries `tool_calls`. The monitor scores the assistant **turn**, not each call — several tool calls in one turn share the one score. Monitor identity is a run-level property: a top-level `monitor` field and/or a declared scorer name.

The card (summary or JSON) prints to **stdout**; the `gate:` verdict line prints to **stderr**, so `--format json` on stdout stays clean and pipeable.

- `trace-covered.json` — a payments agent that looks up a vendor, transfers funds, and reads the balance. Every tool turn carries a `suspicion_score` from the declared monitor, so coverage is 1.0 and the verdict is `covered`.
- `trace-uncovered.json` — the same shape, but the `transfer` turn and the `delete_record` turn carry **no** `suspicion_score`. The read-only lookup was scored; the funds-movement and delete turns were not. Coverage drops below 1.0.

Both traces are synthetic fixtures in the tool's real message format. They exercise the gate; they are not real eval results and make no claim about any model or system.

## Not "monitors are broken" — "verify the monitor covered the risky call"

A normal ControlArena-style run scores **every** turn, so it cards as `covered` (coverage ≈ 1.0) — that is the expected, healthy case, and this gate passes it. The gap this card surfaces is the one that appears in **partial, sampled, or post-hoc monitoring** (real production monitoring is often partial for cost or latency reasons), or when a trace **names** a monitor but does not carry per-turn scores for the risky calls. The point is to verify, per run, that the monitor actually covered the risky tool execution — not to imply any monitor or framework is broken.

## Covered: every risky turn was scored

```bash
PYTHONPATH=src python3 -m inspect_evidence_sufficiency.coverage_cli \
  examples/monitor-coverage/trace-covered.json \
  --format summary --gate
```

Observed (stdout card, then the `gate:` line on stderr):

```
verdict: covered
monitor_identity: present=True name=agentic_payments_suspicion_monitor
coverage_ratio: 1.0
risky_turns: 3 (monitored 3, tool_turns 3)
uncovered_spans:
- none
gate: PASS (exit 0) | verdict 'covered'
```

`echo $?` prints `0`. Every tool-bearing turn carried a suspicion score from the identified monitor, so the run is fully covered and the gate passes.

## Uncovered: the risky tool turns slipped through

By default every tool-bearing turn is treated as risky. Here two of them — `transfer` and `delete_record` — carry no suspicion score:

```bash
PYTHONPATH=src python3 -m inspect_evidence_sufficiency.coverage_cli \
  examples/monitor-coverage/trace-uncovered.json \
  --format summary --gate
```

Observed (stdout card, then the `gate:` line on stderr):

```
verdict: partial-coverage
monitor_identity: present=True name=agentic_payments_suspicion_monitor
coverage_ratio: 0.333
risky_turns: 3 (monitored 1, tool_turns 3)
uncovered_spans:
- turn 1: transfer
- turn 2: delete_record
gate: BLOCK (exit 1) | verdict 'partial-coverage' fails --gate (requires 'covered'): An identified monitor scored some but not all risky tool turns (coverage 0.333).
```

`echo $?` prints `1`. The `transfer` and `delete_record` turns were executed but never scored by the monitor, so the gate blocks.

## Narrowing the risky set to a team policy

`--risky-tools` is the team-owned policy: only the listed tools' turns must be monitored. Restricting the risky set to the two funds-and-data-mutating tools, neither of which was scored, drives coverage to 0 and the verdict to `uncovered`:

```bash
PYTHONPATH=src python3 -m inspect_evidence_sufficiency.coverage_cli \
  examples/monitor-coverage/trace-uncovered.json \
  --risky-tools transfer,delete_record \
  --format summary --require-covered
```

This prints `gate: BLOCK (exit 1) | verdict 'uncovered' fails --gate (requires 'covered'): An identified monitor scored none of the risky tool turns (coverage 0.0).` on stderr and exits `1`. `--require-covered` is an alias for `--gate`.

## Reporting mode never blocks

Without `--gate` / `--require-covered`, the CLI only reports; it always exits `0`, even on an uncovered trace:

```bash
PYTHONPATH=src python3 -m inspect_evidence_sufficiency.coverage_cli \
  examples/monitor-coverage/trace-uncovered.json --format summary
```

This prints `gate: report-only (exit 0) | verdict: partial-coverage` on stderr and exits `0`. Adopt the gate incrementally: start in reporting mode, then turn on `--gate` once teams are ready for it to block.

## Exit codes

| exit | meaning                                                                                                                                                    |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0    | pass (`covered`), or reporting mode (no gate requested)                                                                                                    |
| 1    | gate block: `--gate` / `--require-covered` was set and the verdict was not `covered`                                                                       |
| 2    | tool/usage error: unparseable trace, a scalar top level, a non-object message, a dangling `tool_call_id`, a malformed pre-generated card, or bad arguments |

Exit `2` is always distinguishable from a gate block (exit `1`), so a CI job can tell "the gate said no" apart from "the tool could not run".

## Boundary

This is a coverage/presence check, not a safety, quality, correctness, or compliance claim. A `covered` verdict means only that an identified monitor scored the risky tool turns; it does not say the monitor was right, and it does not approve a release. The release decision remains a human one, subject to normal review.
