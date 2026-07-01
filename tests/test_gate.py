from __future__ import annotations

import unittest
from pathlib import Path

from inspect_evidence_sufficiency.cli import (
    EXIT_GATE_BLOCKED,
    EXIT_OK,
    EXIT_USAGE_ERROR,
    _gate_exit_code,
    _verdict_line,
    main,
)

ROOT = Path(__file__).resolve().parents[1]
GATE_DIR = ROOT / "examples" / "release-gate"
MISSING = GATE_DIR / "trace-missing-monitor.json"
FIXED = GATE_DIR / "trace-with-monitor.json"

RELEASE_DECISION = "Can the billing agent enter a limited internal canary?"
EVAL_OBJECTIVE = "Confirm answers are grounded in the approved knowledge base and no unauthorized tool was used."


def _card(status: str, verdict: str) -> dict:
    """Minimal card shaped like the real thing, for pure mapping tests."""
    return {
        "score_summary": {"overall_verdict": verdict},
        "card_fields": [{"id": "F07", "field": "scorer and monitor identity", "status": status}],
    }


class GateMappingTests(unittest.TestCase):
    def test_report_mode_never_blocks_on_verdict(self) -> None:
        for verdict in ("insufficient", "conditional", "sufficient-for-named-decision"):
            code, _ = _gate_exit_code(_card("present", verdict), "report", [])
            self.assertEqual(code, EXIT_OK)

    def test_gate_blocks_only_insufficient(self) -> None:
        self.assertEqual(_gate_exit_code(_card("present", "insufficient"), "gate", [])[0], EXIT_GATE_BLOCKED)
        self.assertEqual(_gate_exit_code(_card("present", "conditional"), "gate", [])[0], EXIT_OK)
        self.assertEqual(_gate_exit_code(_card("present", "sufficient-for-named-decision"), "gate", [])[0], EXIT_OK)

    def test_strict_also_blocks_conditional(self) -> None:
        self.assertEqual(_gate_exit_code(_card("present", "insufficient"), "strict", [])[0], EXIT_GATE_BLOCKED)
        self.assertEqual(_gate_exit_code(_card("present", "conditional"), "strict", [])[0], EXIT_GATE_BLOCKED)
        self.assertEqual(_gate_exit_code(_card("present", "sufficient-for-named-decision"), "strict", [])[0], EXIT_OK)

    def test_require_present_blocks_when_missing_even_in_report_mode(self) -> None:
        # A required field that is not present blocks regardless of mode/verdict.
        code, reason = _gate_exit_code(_card("missing", "sufficient-for-named-decision"), "report", ["F07"])
        self.assertEqual(code, EXIT_GATE_BLOCKED)
        self.assertIn("F07", reason)

    def test_require_present_passes_when_present(self) -> None:
        code, _ = _gate_exit_code(_card("present", "insufficient"), "report", ["F07"])
        self.assertEqual(code, EXIT_OK)

    def test_verdict_line_reports_block_even_in_report_mode(self) -> None:
        # Regression: a --require-present failure sets exit 1 while the mode is
        # "report"; the printed line must say BLOCK (exit 1), not the misleading
        # "report-only (exit 0)" that would contradict the process exit code.
        line = _verdict_line(
            _card("missing", "conditional"),
            "report",
            EXIT_GATE_BLOCKED,
            "required field(s) not present: F07=missing",
        )
        self.assertIn("BLOCK (exit 1)", line)
        self.assertIn("F07", line)
        self.assertNotIn("report-only", line)

    def test_verdict_line_report_only_when_passing_in_report_mode(self) -> None:
        line = _verdict_line(_card("present", "conditional"), "report", EXIT_OK, "verdict 'conditional'")
        self.assertIn("report-only (exit 0)", line)

    def test_verdict_line_pass_in_gate_mode(self) -> None:
        line = _verdict_line(
            _card("present", "sufficient-for-named-decision"),
            "gate",
            EXIT_OK,
            "verdict 'sufficient-for-named-decision'",
        )
        self.assertIn("PASS (exit 0)", line)


class GateCliTests(unittest.TestCase):
    def test_demo_traces_exist(self) -> None:
        self.assertTrue(MISSING.is_file())
        self.assertTrue(FIXED.is_file())

    def test_plain_gate_blocks_insufficient_raw_trace(self) -> None:
        # Scored as a raw trace (no release decision) the missing-monitor trace is insufficient.
        self.assertEqual(main([str(MISSING), "--gate", "--format", "summary"]), EXIT_GATE_BLOCKED)

    def test_default_reporting_passes_even_when_insufficient(self) -> None:
        self.assertEqual(main([str(MISSING), "--format", "summary"]), EXIT_OK)

    def test_gate_passes_fixed_trace(self) -> None:
        code = main(
            [
                str(FIXED),
                "--release-decision",
                RELEASE_DECISION,
                "--eval-objective",
                EVAL_OBJECTIVE,
                "--gate",
                "--format",
                "summary",
            ]
        )
        self.assertEqual(code, EXIT_OK)

    def test_require_present_f07_blocks_missing_trace(self) -> None:
        code = main(
            [
                str(MISSING),
                "--release-decision",
                RELEASE_DECISION,
                "--eval-objective",
                EVAL_OBJECTIVE,
                "--require-present",
                "F07",
                "--format",
                "summary",
            ]
        )
        self.assertEqual(code, EXIT_GATE_BLOCKED)

    def test_require_present_f07_passes_fixed_trace(self) -> None:
        code = main(
            [
                str(FIXED),
                "--release-decision",
                RELEASE_DECISION,
                "--eval-objective",
                EVAL_OBJECTIVE,
                "--require-present",
                "F07",
                "--format",
                "summary",
            ]
        )
        self.assertEqual(code, EXIT_OK)

    def test_strict_blocks_conditional_fixed_trace(self) -> None:
        code = main(
            [
                str(FIXED),
                "--release-decision",
                RELEASE_DECISION,
                "--eval-objective",
                EVAL_OBJECTIVE,
                "--gate",
                "--strict",
                "--format",
                "summary",
            ]
        )
        self.assertEqual(code, EXIT_GATE_BLOCKED)

    def test_unknown_field_id_is_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main([str(MISSING), "--require-present", "F99", "--format", "summary"])
        self.assertEqual(ctx.exception.code, EXIT_USAGE_ERROR)

    def test_broken_input_is_usage_error(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.json"
            broken.write_text("not json {{{", encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                main([str(broken), "--gate", "--format", "summary"])
            self.assertEqual(ctx.exception.code, EXIT_USAGE_ERROR)

    def test_lowercase_field_id_is_accepted(self) -> None:
        # Field ids are normalized to uppercase; f07 == F07.
        code = main(
            [
                str(MISSING),
                "--release-decision",
                RELEASE_DECISION,
                "--require-present",
                "f07",
                "--format",
                "summary",
            ]
        )
        self.assertEqual(code, EXIT_GATE_BLOCKED)


class MalformedInputTests(unittest.TestCase):
    """Deeply-nested traces and malformed pre-generated cards exit 2, never 1/traceback."""

    def test_deeply_nested_trace_is_usage_error(self) -> None:
        import json
        import tempfile

        # Nest well past MAX_TRACE_DEPTH; previously this raised RecursionError that
        # escaped as a traceback with exit 1 (indistinguishable from a gate block).
        node: dict = {}
        cur = node
        for _ in range(4000):
            cur["a"] = {}
            cur = cur["a"]
        cur["a"] = 1
        with tempfile.TemporaryDirectory() as tmp:
            deep = Path(tmp) / "deep.json"
            deep.write_text(json.dumps(node), encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                main([str(deep), "--gate", "--format", "summary"])
            self.assertEqual(ctx.exception.code, EXIT_USAGE_ERROR)

    def test_malformed_pregenerated_card_is_usage_error(self) -> None:
        import json
        import tempfile

        # A file that claims the card schema but lacks required top-level keys used to
        # crash summarize_card with an uncaught KeyError (traceback, exit 1).
        bad_card = {"schema": "evidence-sufficiency-card.v0", "version": 1, "created_utc": "2026-06-30T12:00:00+00:00"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "card.json"
            path.write_text(json.dumps(bad_card), encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                main([str(path), "--gate", "--format", "summary"])
            self.assertEqual(ctx.exception.code, EXIT_USAGE_ERROR)

    def test_scalar_top_level_is_usage_error(self) -> None:
        import tempfile

        # A bare JSON scalar (null / number / string) is not a trace; it must be a
        # usage error (exit 2), not a silently-scored insufficient card.
        for scalar in ("null", "42", '"just a string"'):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "scalar.json"
                path.write_text(scalar, encoding="utf-8")
                with self.assertRaises(SystemExit) as ctx:
                    main([str(path), "--gate", "--format", "summary"])
                self.assertEqual(ctx.exception.code, EXIT_USAGE_ERROR, f"input {scalar!r} should be a usage error")


class StdoutHygieneTests(unittest.TestCase):
    """`--format json` stdout must be a clean, parseable card; the gate line is stderr."""

    def test_json_format_stdout_parses_as_json(self) -> None:
        import contextlib
        import io
        import json

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main([str(FIXED), "--format", "json"])
        self.assertEqual(code, EXIT_OK)
        # The trailing "gate:" line must not be on stdout; the card must round-trip.
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed["schema"], "evidence-sufficiency-card.v0")

    def test_gate_line_goes_to_stderr(self) -> None:
        import contextlib
        import io

        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            main([str(MISSING), "--format", "summary"])
        self.assertIn("gate:", err.getvalue())
        self.assertNotIn("gate:", out.getvalue())


if __name__ == "__main__":
    unittest.main()
