"""Command line interface for Evidence-Sufficiency-Card generation.

Exit-code contract (so this CLI can act as a CI deployment gate):

- ``2`` is reserved for tool/usage errors: an unparseable trace, a trace that
  nests deeper than ``extract.MAX_TRACE_DEPTH``, a malformed pre-generated card
  (missing required top-level keys), bad arguments, or an unknown evidence-field
  id. A usage error is always distinguishable from a gate block.
- ``1`` is a gate block: the requested gate condition was not met.
- ``0`` is success, or reporting mode (no gate requested).

The card itself is printed to stdout; the ``gate:`` verdict line is printed to
stderr, so ``--format json`` yields a clean, parseable card on stdout (the exit
code, not the printed line, is the machine-readable gate signal). A ``.json``
input that already declares the card schema is trusted and passed through
verbatim — its own verdict is used without recomputation.

The mapping from the card verdict to a gate exit code:

| mode                       | insufficient | conditional | sufficient-for-named-decision |
| -------------------------- | ------------ | ----------- | ----------------------------- |
| default (no ``--gate``)    | 0            | 0           | 0                             |
| ``--gate``                 | 1            | 0           | 0                             |
| ``--gate --strict``        | 1            | 1           | 0                             |

``--require-present F06,F07`` blocks (exit ``1``) in any mode if any listed field
is not ``present``. Gate blocks never mask a usage error: unknown field ids are a
usage error (exit ``2``), not a gate block.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .card import FIELD_LABELS, build_card, summarize_card
from .gatelib import (
    EXIT_GATE_BLOCKED,
    EXIT_OK,
    EXIT_USAGE_ERROR,
    GateUsageError,
    verdict_line,
)

# Re-exported so existing importers (and tests) can keep importing the exit-code
# constants and GateUsageError from this module; the definitions live in gatelib,
# shared with the monitor-coverage gate.
__all__ = ["EXIT_GATE_BLOCKED", "EXIT_OK", "EXIT_USAGE_ERROR", "GateUsageError", "main"]

# Top-level keys a passed-through pre-generated card must carry for the CLI to
# summarize and gate on it without recomputation. A card that claims the schema
# but lacks these is malformed input, not a gate block.
REQUIRED_CARD_KEYS = ("score_summary", "card_fields")

# Verdict strings owned by card.py. Re-derived here for the gate mapping only; the
# card itself remains the source of truth for how a verdict is assigned.
VERDICT_INSUFFICIENT = "insufficient"
VERDICT_CONDITIONAL = "conditional"
VERDICT_SUFFICIENT = "sufficient-for-named-decision"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        require_present = _parse_require_present(args.require_present)
        card = _load_or_build_card(args)
    except GateUsageError as exc:
        parser.exit(EXIT_USAGE_ERROR, f"error: {exc}\n")
    except RecursionError:
        # A pathologically deep / adversarial trace exhausted the recursion limit.
        # Map it to a usage error so it is never mistaken for a gate block (exit 1).
        parser.exit(EXIT_USAGE_ERROR, f"error: could not process trace {args.input!r}: input nesting is too deep\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # Unparseable / unreadable trace, or malformed JSON/JSONL.
        parser.exit(EXIT_USAGE_ERROR, f"error: could not read or parse trace {args.input!r}: {exc}\n")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(card, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.format == "summary":
        print(summarize_card(card))
    else:
        print(json.dumps(card, indent=2, sort_keys=True))

    gate_mode = _resolve_gate_mode(args)
    exit_code, reason = _gate_exit_code(card, gate_mode, require_present)
    # The gate verdict line goes to STDERR so stdout stays a clean, parseable card
    # (`--format json` can be piped straight into `jq`); the exit code is the
    # machine-readable gate signal.
    print(_verdict_line(card, gate_mode, exit_code, reason), file=sys.stderr)
    return exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an Evidence-Sufficiency-Card from an agent eval trace, "
        "optionally acting as a CI deployment gate via its exit code.",
    )
    parser.add_argument("input", help="JSON, JSONL, generated card JSON, or ControlArena export directory")
    parser.add_argument("--source-url", default=None)
    parser.add_argument("--source-title", default=None)
    parser.add_argument("--release-decision", default=None)
    parser.add_argument("--eval-objective", default=None)
    parser.add_argument("--output", "-o", default=None, help="Write JSON card to this path")
    parser.add_argument("--format", choices=["json", "summary"], default="json")

    gate = parser.add_argument_group("deployment gate")
    gate.add_argument(
        "--gate",
        action="store_true",
        help="Exit 1 when the card verdict is 'insufficient' (blocks a deployment). "
        "Without this flag the CLI only reports and always exits 0.",
    )
    gate.add_argument(
        "--strict",
        action="store_true",
        help="With --gate, also exit 1 on a 'conditional' verdict (only "
        "'sufficient-for-named-decision' passes). No effect without --gate.",
    )
    gate.add_argument(
        "--require-present",
        default=None,
        metavar="F06,F07,...",
        help="Comma-separated evidence-field ids that must each be 'present'; "
        "exit 1 if any is 'partial' or 'missing'. Applies in any mode. "
        "An unknown field id is a usage error (exit 2).",
    )
    return parser


def _load_or_build_card(args: argparse.Namespace) -> dict[str, Any]:
    """Return the card for ``args.input``.

    A ``.json`` file that declares ``schema == "evidence-sufficiency-card.v0"`` is
    trusted as a pre-generated card and passed through **verbatim** — its own
    verdict and field statuses are used as-is, with no recomputation. Such a card
    must still carry the required top-level keys; a malformed one is a usage error
    (exit 2), not a gate block, so it never crashes downstream summarization.
    """
    input_path = Path(args.input)
    if input_path.is_file() and input_path.suffix == ".json":
        maybe_card = _read_json(input_path)
        if isinstance(maybe_card, dict) and maybe_card.get("schema") == "evidence-sufficiency-card.v0":
            missing = [key for key in REQUIRED_CARD_KEYS if key not in maybe_card]
            if missing:
                raise GateUsageError(
                    f"pre-generated card {args.input!r} is missing required key(s): {', '.join(missing)}"
                )
            return maybe_card
    return build_card(
        input_path,
        release_decision=args.release_decision,
        eval_objective=args.eval_objective,
        source_url=args.source_url,
        source_title=args.source_title,
    )


def _resolve_gate_mode(args: argparse.Namespace) -> str:
    """Reporting by default; 'gate'/'strict' only when --gate is requested."""
    if not args.gate:
        return "report"
    return "strict" if args.strict else "gate"


def _parse_require_present(raw: str | None) -> list[str]:
    """Parse and validate --require-present field ids against the real F01..F12 set."""
    if not raw:
        return []
    ids: list[str] = []
    for token in raw.split(","):
        field_id = token.strip().upper()
        if not field_id:
            continue
        if field_id not in FIELD_LABELS:
            known = ", ".join(sorted(FIELD_LABELS))
            raise GateUsageError(f"unknown evidence-field id {field_id!r} in --require-present (known: {known})")
        ids.append(field_id)
    return ids


def _field_statuses(card: dict[str, Any]) -> dict[str, str]:
    return {field["id"]: field["status"] for field in card.get("card_fields", [])}


def _gate_exit_code(card: dict[str, Any], mode: str, require_present: list[str]) -> tuple[int, str]:
    """Map a card + gate mode + required-present fields to an exit code and reason.

    ``mode`` is one of ``report`` (never blocks on verdict), ``gate`` (blocks on
    ``insufficient``), or ``strict`` (blocks on ``insufficient`` or
    ``conditional``). ``--require-present`` is checked in every mode, including
    ``report``.
    """
    statuses = _field_statuses(card)

    not_present = [fid for fid in require_present if statuses.get(fid) != "present"]
    if not_present:
        detail = ", ".join(f"{fid}={statuses.get(fid, 'unknown')}" for fid in not_present)
        return EXIT_GATE_BLOCKED, f"required field(s) not present: {detail}"

    verdict = card.get("score_summary", {}).get("overall_verdict", "")
    if mode == "gate" and verdict == VERDICT_INSUFFICIENT:
        return EXIT_GATE_BLOCKED, f"verdict '{verdict}' fails --gate"
    if mode == "strict" and verdict in {VERDICT_INSUFFICIENT, VERDICT_CONDITIONAL}:
        return EXIT_GATE_BLOCKED, f"verdict '{verdict}' fails --gate --strict"
    return EXIT_OK, f"verdict '{verdict}'"


def _verdict_line(card: dict[str, Any], mode: str, exit_code: int, reason: str) -> str:
    verdict = card.get("score_summary", {}).get("overall_verdict", "")
    return verdict_line(verdict, mode, exit_code, reason)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
