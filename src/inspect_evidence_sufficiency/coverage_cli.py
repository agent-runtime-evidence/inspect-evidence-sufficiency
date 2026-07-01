"""Command line interface for Monitor-Coverage-Card generation.

The sibling of the Evidence-Sufficiency-Card gate. It answers a sharper question:
did an identified monitor actually score the assistant turns that executed the
risky tools? It reuses the same exit-code contract (``gatelib``):

- ``2`` — tool/usage error: an unparseable trace, a scalar top level, a message
  that is not an object, a ``tool_call_id`` that references no known tool call, a
  malformed pre-generated card, or bad arguments. Always distinguishable from a
  gate block.
- ``1`` — a gate block: with ``--gate`` (or ``--require-covered``), the verdict was
  not ``covered``.
- ``0`` — success, or reporting mode (no gate requested).

The card / summary prints to stdout; the ``gate:`` verdict line prints to stderr,
so ``--format json`` yields a clean, parseable card on stdout (the exit code, not
the printed line, is the machine-readable gate signal).

| mode                    | uncovered | partial-coverage | covered |
| ----------------------- | --------- | ---------------- | ------- |
| default (no ``--gate``) | 0         | 0                | 0       |
| ``--gate``              | 1         | 1                | 0       |

``--require-covered`` is an alias for ``--gate``: the whole point of this gate is to
require that the risky turns were covered, so the two are the same switch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .coverage import (
    SCHEMA,
    VERDICT_COVERED,
    MonitorCoverageError,
    build_coverage_card,
    summarize_coverage_card,
)
from .gatelib import (
    EXIT_GATE_BLOCKED,
    EXIT_OK,
    EXIT_USAGE_ERROR,
    GateUsageError,
    verdict_line,
)

__all__ = ["EXIT_GATE_BLOCKED", "EXIT_OK", "EXIT_USAGE_ERROR", "main"]

# Top-level keys a passed-through pre-generated card must carry for the CLI to
# summarize and gate on it without recomputation. A card that claims the schema
# but lacks these is malformed input, not a gate block.
REQUIRED_CARD_KEYS = ("verdict", "coverage_ratio", "monitor_identity")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        card = _load_or_build_card(args)
    except GateUsageError as exc:
        parser.exit(EXIT_USAGE_ERROR, f"error: {exc}\n")
    except MonitorCoverageError as exc:
        parser.exit(EXIT_USAGE_ERROR, f"error: could not read monitor-coverage trace {args.input!r}: {exc}\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(EXIT_USAGE_ERROR, f"error: could not read or parse trace {args.input!r}: {exc}\n")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(card, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.format == "summary":
        print(summarize_coverage_card(card))
    else:
        print(json.dumps(card, indent=2, sort_keys=True))

    gate_requested = args.gate or args.require_covered
    mode = "gate" if gate_requested else "report"
    exit_code, reason = _gate_exit_code(card, mode)
    print(verdict_line(card.get("verdict", ""), mode, exit_code, reason), file=sys.stderr)
    return exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a Monitor-Coverage-Card from a monitor-coverage trace: did an identified "
        "monitor score the turns that executed the risky tools? Coverage is not accuracy. "
        "Optionally acts as a CI deployment gate via its exit code.",
    )
    parser.add_argument("input", help="JSON or JSONL monitor-coverage trace, or a generated card JSON")
    parser.add_argument(
        "--risky-tools",
        default=None,
        metavar="transfer,delete,...",
        help="Comma-separated tool names whose executing turns must be monitored (team-owned policy). "
        "Default: every tool-bearing turn is treated as risky ('all-tools').",
    )
    parser.add_argument("--monitor", default=None, help="Declare/override the run-level monitor identity name")
    parser.add_argument("--source-url", default=None)
    parser.add_argument("--source-title", default=None)
    parser.add_argument("--output", "-o", default=None, help="Write JSON card to this path")
    parser.add_argument("--format", choices=["json", "summary"], default="json")

    gate = parser.add_argument_group("deployment gate")
    gate.add_argument(
        "--gate",
        action="store_true",
        help="Exit 1 unless the verdict is 'covered' (blocks a deployment whose risky tool turns "
        "were not all scored by an identified monitor). Without this flag the CLI only reports and "
        "always exits 0.",
    )
    gate.add_argument(
        "--require-covered",
        action="store_true",
        help="Alias for --gate: require a 'covered' verdict (exit 1 on 'partial-coverage' or 'uncovered').",
    )
    return parser


def _load_or_build_card(args: argparse.Namespace) -> dict[str, Any]:
    """Return the card for ``args.input``.

    A ``.json`` file that declares ``schema == "monitor-coverage-card.v0"`` is
    trusted as a pre-generated card and passed through **verbatim** — its own
    verdict is used as-is, with no recomputation. Such a card must still carry the
    required top-level keys; a malformed one is a usage error (exit 2), not a gate
    block, so it never crashes downstream summarization.
    """
    input_path = Path(args.input)
    if input_path.is_file() and input_path.suffix == ".json":
        maybe_card = json.loads(input_path.read_text(encoding="utf-8"))
        if isinstance(maybe_card, dict) and maybe_card.get("schema") == SCHEMA:
            missing = [key for key in REQUIRED_CARD_KEYS if key not in maybe_card]
            if missing:
                raise GateUsageError(
                    f"pre-generated card {args.input!r} is missing required key(s): {', '.join(missing)}"
                )
            return maybe_card
    if input_path.suffix == ".eval":
        # A real Inspect .eval log: read the per-turn suspicion scores via the
        # optional [inspect] extra (imported only on this path). Without the extra
        # this is a usage error with an install hint, not a crash.
        from .inspect_coverage import read_eval_log_coverage

        try:
            return read_eval_log_coverage(
                input_path,
                risky_tools=_parse_risky_tools(args.risky_tools),
                monitor_name=args.monitor,
                source_url=args.source_url,
                source_title=args.source_title,
            )
        except RuntimeError as exc:
            raise GateUsageError(str(exc)) from exc
    return build_coverage_card(
        input_path,
        risky_tools=_parse_risky_tools(args.risky_tools),
        monitor_name=args.monitor,
        source_url=args.source_url,
        source_title=args.source_title,
    )


def _parse_risky_tools(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [token.strip() for token in raw.split(",") if token.strip()]


def _gate_exit_code(card: dict[str, Any], mode: str) -> tuple[int, str]:
    """Map a card + gate mode to an exit code and reason.

    ``report`` never blocks; ``gate`` blocks unless the verdict is ``covered``.
    """
    verdict = card.get("verdict", "")
    if mode == "gate" and verdict != VERDICT_COVERED:
        reason = card.get("verdict_reason", "")
        detail = f": {reason}" if reason else ""
        return EXIT_GATE_BLOCKED, f"verdict '{verdict}' fails --gate (requires 'covered'){detail}"
    return EXIT_OK, f"verdict '{verdict}'"


if __name__ == "__main__":
    raise SystemExit(main())
