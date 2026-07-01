"""Shared gate exit-code machinery for the CLI deployment gates.

Both the Evidence-Sufficiency-Card gate (``cli``) and the Monitor-Coverage-Card
gate (``coverage_cli``) act as CI deployment gates through their exit code. They
share one contract so a CI job reads the same signal from either:

- ``0`` — success, or reporting mode (no gate requested);
- ``1`` — a gate block: the requested gate condition was not met;
- ``2`` — a tool/usage error (unparseable trace, bad arguments, ...), always
  distinguishable from a gate block.

The card / summary is printed to stdout; the ``gate:`` verdict line is printed to
stderr, so ``--format json`` yields a clean, parseable card on stdout (the exit
code, not the printed line, is the machine-readable gate signal).
"""

from __future__ import annotations

# Reserved exit codes. Kept distinct so a CI job can tell a gate block (1) from a
# tool/usage error (2).
EXIT_OK = 0
EXIT_GATE_BLOCKED = 1
EXIT_USAGE_ERROR = 2


class GateUsageError(Exception):
    """A tool/usage error that must surface as exit code 2, not a gate block."""


def verdict_line(verdict: str, mode: str, exit_code: int, reason: str) -> str:
    """Format the single ``gate:`` line printed to stderr by every gate CLI.

    A non-zero exit always reports as a ``BLOCK`` with its real exit code and
    reason — even in reporting mode, where a ``--require-*`` failure (not
    ``--gate``) is what set the non-zero exit — so the printed line never claims
    ``report-only (exit 0)`` while the process exits non-zero.
    """
    if exit_code != EXIT_OK:
        return f"gate: BLOCK (exit {exit_code}) | {reason}"
    if mode == "report":
        return f"gate: report-only (exit 0) | verdict: {verdict}"
    return f"gate: PASS (exit 0) | {reason}"
